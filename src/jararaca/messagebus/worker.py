# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import inspect
import logging
import random
import signal
import time
import uuid
from abc import ABC
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncContextManager, AsyncGenerator, Awaitable, Optional, Type
from urllib.parse import parse_qs, urlparse

import aio_pika
import aio_pika.abc
import uvloop
from aio_pika.exceptions import (
    AMQPChannelError,
    AMQPConnectionError,
    AMQPError,
    ChannelClosed,
    ChannelNotFoundEntity,
    ConnectionClosed,
)
from pydantic import ValidationError

from jararaca.broker_backend import MessageBrokerBackend
from jararaca.broker_backend.mapper import get_message_broker_backend_from_url
from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.lifecycle import AppLifecycle
from jararaca.messagebus.bus_message_controller import (
    BusMessageController,
    provide_bus_message_controller,
)
from jararaca.messagebus.decorators import (
    MESSAGE_HANDLER_DATA_SET,
    SCHEDULED_ACTION_DATA_SET,
    MessageBusController,
    MessageHandler,
    MessageHandlerData,
    ScheduleDispatchData,
)
from jararaca.messagebus.implicit_headers import provide_implicit_headers
from jararaca.messagebus.message import Message, MessageOf
from jararaca.microservice import (
    AppTransactionContext,
    MessageBusTransactionData,
    Microservice,
    SchedulerTransactionData,
    ShutdownState,
    provide_shutdown_state,
    providing_app_type,
)
from jararaca.observability.hooks import record_exception, set_span_status
from jararaca.scheduler.decorators import ScheduledActionData
from jararaca.utils.rabbitmq_utils import RabbitmqUtils
from jararaca.utils.retry import RetryPolicy, retry_with_backoff

logger = logging.getLogger(__name__)


@dataclass
class AioPikaWorkerConfig:
    url: str
    exchange: str
    prefetch_count: int
    connection_retry_config: RetryPolicy = field(
        default_factory=lambda: RetryPolicy(
            max_retries=15,
            initial_delay=1.0,
            max_delay=60.0,
            backoff_factor=2.0,
        )
    )
    consumer_retry_policy: RetryPolicy = field(
        default_factory=lambda: RetryPolicy(
            max_retries=15,
            initial_delay=0.5,
            max_delay=40.0,
            backoff_factor=2.0,
        )
    )
    # Connection health monitoring settings
    connection_heartbeat_interval: float = 30.0  # seconds
    connection_health_check_interval: float = 10.0  # seconds


class AioPikaMessage(MessageOf[Message]):

    def __init__(
        self,
        aio_pika_message: aio_pika.abc.AbstractIncomingMessage,
        model_type: Type[Message],
    ):
        self.aio_pika_message = aio_pika_message
        self.model_type = model_type

    def payload(self) -> Message:
        return self.model_type.model_validate_json(self.aio_pika_message.body)


class MessageProcessingLocker:

    def __init__(self) -> None:
        self.messages_lock = asyncio.Lock()
        self.current_processing_messages_set: set[asyncio.Task[Any]] = set()

    @asynccontextmanager
    async def lock_message_task(
        self, task: asyncio.Task[Any]
    ) -> AsyncGenerator[None, Any]:
        async with self.messages_lock:
            self.current_processing_messages_set.add(task)
            try:
                yield
            finally:
                self.current_processing_messages_set.discard(task)

    async def wait_all_messages_processed(self) -> None:
        if len(self.current_processing_messages_set) == 0:
            return

        await asyncio.gather(*self.current_processing_messages_set)


class MessageBusConsumer(ABC):

    async def consume(self) -> None:
        raise NotImplementedError("consume method not implemented")

    def shutdown(self) -> None: ...

    async def close(self) -> None:
        """Close all resources related to the consumer"""


class _WorkerShutdownState(ShutdownState):
    def __init__(self, shutdown_event: asyncio.Event):
        self.shutdown_event = shutdown_event

    def request_shutdown(self) -> None:
        self.shutdown_event.set()

    def is_shutdown_requested(self) -> bool:
        return self.shutdown_event.is_set()

    async def wait_for_shutdown(self) -> None:
        await self.shutdown_event.wait()


class AioPikaMicroserviceConsumer(MessageBusConsumer):
    def __init__(
        self,
        broker_backend: MessageBrokerBackend,
        config: AioPikaWorkerConfig,
        message_handler_set: MESSAGE_HANDLER_DATA_SET,
        scheduled_actions: SCHEDULED_ACTION_DATA_SET,
        uow_context_provider: UnitOfWorkContextProvider,
    ):

        self.broker_backend = broker_backend
        self.config = config
        self.message_handler_set = message_handler_set
        self.scheduled_actions = scheduled_actions
        self.incoming_map: dict[str, MessageHandlerData] = {}
        self.uow_context_provider = uow_context_provider
        self.shutdown_event = asyncio.Event()
        self.shutdown_state = _WorkerShutdownState(self.shutdown_event)
        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.connection: aio_pika.abc.AbstractConnection | None = None
        self.channels: dict[str, aio_pika.abc.AbstractChannel] = {}

        # Connection resilience attributes
        self.connection_healthy = False
        self.connection_lock = asyncio.Lock()
        self.consumer_tags: dict[str, str] = {}  # Track consumer tags for cleanup
        self.health_check_task: asyncio.Task[Any] | None = None

    async def _verify_infrastructure(self) -> bool:
        """
        Verify that the required RabbitMQ infrastructure (exchanges, queues) exists.
        Returns True if all required infrastructure is in place.
        """
        try:
            async with self.connect() as connection:
                # Create a main channel just for checking infrastructure
                async with connection.channel() as main_channel:
                    # Get existing exchange and queues to verify infrastructure is in place
                    await RabbitmqUtils.get_main_exchange(
                        channel=main_channel,
                        exchange_name=self.config.exchange,
                    )
                    await RabbitmqUtils.get_dl_exchange(channel=main_channel)
                    await RabbitmqUtils.get_dl_queue(channel=main_channel)
                    return True
        except (ChannelNotFoundEntity, ChannelClosed, AMQPError) as e:
            logger.critical(
                f"Required exchange or queue infrastructure not found. "
                f"Please use the declare command first to create the required infrastructure. Error: {e}"
            )
            return False

    async def _setup_message_handler_consumer(
        self, handler: MessageHandlerData
    ) -> bool:
        """
        Set up a consumer for a message handler with retry mechanism.
        Returns True if successful, False otherwise.
        """
        queue_name = f"{handler.message_type.MESSAGE_TOPIC}.{handler.instance_callable.__module__}.{handler.instance_callable.__qualname__}"
        routing_key = f"{handler.message_type.MESSAGE_TOPIC}.#"

        async def setup_consumer() -> None:
            # Create a channel using the context manager
            async with self.create_channel(queue_name) as channel:
                queue: aio_pika.abc.AbstractQueue = await RabbitmqUtils.get_queue(
                    channel=channel, queue_name=queue_name
                )

                # Configure consumer and get the consumer tag
                consumer_tag = await queue.consume(
                    callback=MessageHandlerCallback(
                        consumer=self,
                        queue_name=queue_name,
                        routing_key=routing_key,
                        message_handler=handler,
                    ),
                    # no_ack=handler.spec.auto_ack,
                )

                # Store consumer tag for cleanup
                self.consumer_tags[queue_name] = consumer_tag

                logger.info(
                    "Consuming message handler %s on dedicated channel", queue_name
                )

                await self.shutdown_event.wait()

                logger.warning(
                    "Shutdown event received, stopping consumer for %s", queue_name
                )
                await queue.cancel(consumer_tag)

        try:
            # Setup with retry
            await retry_with_backoff(
                setup_consumer,
                retry_policy=self.config.consumer_retry_policy,
                retry_exceptions=(
                    ChannelNotFoundEntity,
                    ChannelClosed,
                    AMQPError,
                    AMQPConnectionError,
                    AMQPChannelError,
                    ConnectionClosed,
                ),
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to setup consumer for queue '{queue_name}' after retries: {e}"
            )
            return False

    async def _setup_scheduled_action_consumer(
        self, scheduled_action: ScheduledActionData
    ) -> bool:
        """
        Set up a consumer for a scheduled action with retry mechanism.
        Returns True if successful, False otherwise.
        """
        queue_name = f"{scheduled_action.callable.__module__}.{scheduled_action.callable.__qualname__}"
        routing_key = queue_name

        async def setup_consumer() -> None:
            # Create a channel using the context manager
            async with self.create_channel(queue_name) as channel:
                queue = await RabbitmqUtils.get_queue(
                    channel=channel, queue_name=queue_name
                )

                # Configure consumer and get the consumer tag
                consumer_tag = await queue.consume(
                    callback=ScheduledMessageHandlerCallback(
                        consumer=self,
                        queue_name=queue_name,
                        routing_key=routing_key,
                        scheduled_action=scheduled_action,
                    ),
                    no_ack=True,
                )

                # Store consumer tag for cleanup
                self.consumer_tags[queue_name] = consumer_tag

                logger.debug("Consuming scheduler %s on dedicated channel", queue_name)

                await self.shutdown_event.wait()

                logger.warning(
                    "Shutdown event received, stopping consumer for %s", queue_name
                )
                await queue.cancel(consumer_tag)

        try:
            # Setup with retry
            await retry_with_backoff(
                setup_consumer,
                retry_policy=self.config.consumer_retry_policy,
                retry_exceptions=(
                    ChannelNotFoundEntity,
                    ChannelClosed,
                    AMQPError,
                    AMQPConnectionError,
                    AMQPChannelError,
                    ConnectionClosed,
                ),
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to setup consumer for scheduler queue '{queue_name}' after retries: {e}"
            )
            return False

    async def consume(self) -> None:
        """
        Main consume method that sets up all message handlers and scheduled actions with retry mechanisms.
        """
        # Establish initial connection
        try:
            async with self.connect() as connection:
                self.connection_healthy = True

                # Start connection health monitoring
                self.health_check_task = asyncio.create_task(
                    self._monitor_connection_health(), name="ConnectionHealthMonitor"
                )

                # Verify infrastructure with retry
                infra_check_success = await retry_with_backoff(
                    self._verify_infrastructure,
                    retry_policy=self.config.connection_retry_config,
                    retry_exceptions=(Exception,),
                )

                if not infra_check_success:
                    logger.critical(
                        "Failed to verify RabbitMQ infrastructure. Shutting down."
                    )
                    self.shutdown_event.set()
                    return

                async def wait_for(
                    type: str, name: str, coroutine: Awaitable[bool]
                ) -> tuple[str, str, bool]:
                    return type, name, await coroutine

                tasks: set[asyncio.Task[tuple[str, str, bool]]] = set()

                # Setup message handlers
                for handler in self.message_handler_set:
                    queue_name = f"{handler.message_type.MESSAGE_TOPIC}.{handler.instance_callable.__module__}.{handler.instance_callable.__qualname__}"
                    self.incoming_map[queue_name] = handler

                    tasks.add(
                        task := asyncio.create_task(
                            wait_for(
                                "message_handler",
                                queue_name,
                                self._setup_message_handler_consumer(handler),
                            ),
                            name=f"MessageHandler-{queue_name}-setup-consumer",
                        )
                    )

                # Setup scheduled actions
                for scheduled_action in self.scheduled_actions:
                    queue_name = f"{scheduled_action.callable.__module__}.{scheduled_action.callable.__qualname__}"
                    tasks.add(
                        task := asyncio.create_task(
                            wait_for(
                                "scheduled_action",
                                queue_name,
                                self._setup_scheduled_action_consumer(scheduled_action),
                            ),
                            name=f"ScheduledAction-{queue_name}-setup-consumer",
                        )
                    )

                async def handle_task_results() -> None:
                    for task in asyncio.as_completed(tasks):
                        type, name, success = await task
                        if success:
                            logger.debug(
                                "Successfully set up %s consumer for %s", type, name
                            )
                        else:
                            logger.warning(
                                "Failed to set up %s consumer for %s, will not process messages from this queue",
                                type,
                                name,
                            )

                handle_task_results_task = asyncio.create_task(
                    handle_task_results(), name="HandleSetupTaskResults"
                )

                # Wait for shutdown signal
                await self.shutdown_event.wait()
                logger.debug("Shutdown event received, stopping consumers")

                await self.cancel_queue_consumers()

                # Cancel health monitoring
                if self.health_check_task:
                    self.health_check_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self.health_check_task

                handle_task_results_task.cancel()
                with suppress(asyncio.CancelledError):
                    await handle_task_results_task
                for task in tasks:
                    if not task.done():
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                logger.debug("Worker shutting down")
                # Wait for all tasks to complete
                await self.wait_all_tasks_done()

                # Close all channels and the connection
                await self.close_channels_and_connection()

        except Exception as e:
            logger.critical("Failed to establish initial connection to RabbitMQ: %s", e)
            # Re-raise the exception so it can be caught by the caller
            raise

    async def cancel_queue_consumers(self) -> None:
        """
        Cancel all active queue consumers.
        """
        logger.debug("Cancelling all active queue consumers...")
        for queue_name, channel in self.channels.items():
            try:
                if not channel.is_closed:
                    # Cancel consumer if we have its tag
                    if queue_name in self.consumer_tags:
                        try:
                            queue = await channel.get_queue(queue_name, ensure=False)
                            if queue:
                                await queue.cancel(self.consumer_tags[queue_name])
                        except Exception as cancel_error:
                            logger.warning(
                                "Error cancelling consumer for %s: %s",
                                queue_name,
                                cancel_error,
                            )
                        del self.consumer_tags[queue_name]
            except Exception as e:
                logger.warning("Error cancelling consumer for %s: %s", queue_name, e)

    async def wait_all_tasks_done(self) -> None:
        if not self.tasks:
            return

        logger.warning(
            "Waiting for (%s) in-flight tasks to complete: %s",
            len(self.tasks),
            ", ".join((task.get_name()) for task in self.tasks),
        )
        # async with self.lock:
        # Use gather with return_exceptions=True to ensure all tasks are awaited
        # even if some raise exceptions
        # results = await asyncio.gather(*self.tasks, return_exceptions=True)
        pending_tasks = [task for task in self.tasks if not task.done()]
        while len(pending_tasks) > 0:
            if not pending_tasks:
                break
            await asyncio.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)

            pending_tasks = [task for task in pending_tasks if not task.done()]
            if len(pending_tasks) > 0:
                logger.warning(
                    "Waiting for (%s) in-flight tasks to complete: %s",
                    len(pending_tasks),
                    ", ".join((task.get_name()) for task in pending_tasks),
                )

        logger.warning("All in-flight tasks have completed.")
        # Log any exceptions that occurred
        # for result in results:
        #     if isinstance(result, Exception):
        #         logger.error("Task raised an exception during shutdown: %s", result)

    async def close_channels_and_connection(self) -> None:
        """Close all channels and then the connection"""
        logger.warning("Closing channels and connection...")
        await self._cleanup_connection()

    def shutdown(self) -> None:
        """Signal for shutdown"""
        logger.warning("Initiating graceful shutdown")
        self.shutdown_event.set()

    async def close(self) -> None:
        """Implement MessageBusConsumer.close for cleanup"""
        logger.warning("Closing consumer...")
        self.shutdown()

        # Cancel health monitoring
        if self.health_check_task:
            self.health_check_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.health_check_task

        await self.wait_all_tasks_done()
        await self.close_channels_and_connection()

    async def get_channel(self, queue_name: str) -> aio_pika.abc.AbstractChannel | None:
        """
        Get the channel for a specific queue, or None if not found.
        This helps with error handling when a channel might have been closed.
        """
        if queue_name not in self.channels:
            logger.warning("No channel found for queue %s", queue_name)
            return None

        try:
            channel = self.channels[queue_name]
            if channel.is_closed:
                logger.warning("Channel for queue %s is closed", queue_name)
                # Remove the closed channel
                del self.channels[queue_name]

                # Attempt to recreate the channel if connection is healthy
                if (
                    self.connection
                    and not self.connection.is_closed
                    and self.connection_healthy
                ):
                    try:
                        logger.debug("Creating new channel for %s", queue_name)
                        self.channels[queue_name] = await self.connection.channel()
                        await self.channels[queue_name].set_qos(
                            prefetch_count=self.config.prefetch_count
                        )
                        return self.channels[queue_name]
                    except Exception as e:
                        logger.error(
                            "Failed to recreate channel for %s: %s", queue_name, e
                        )
                        # Trigger shutdown if channel creation fails
                        self._trigger_shutdown()
                        return None
                else:
                    # Connection is not healthy, trigger shutdown
                    self._trigger_shutdown()
                    return None
            return channel
        except Exception as e:
            logger.error("Error accessing channel for queue %s: %s", queue_name, e)
            # Trigger shutdown on any channel access error
            self._trigger_shutdown()
            return None

    async def _establish_channel(self, queue_name: str) -> aio_pika.abc.AbstractChannel:
        """
        Creates a new channel for the specified queue with proper QoS settings.
        """
        if self.connection is None or self.connection.is_closed:
            logger.warning(
                "Cannot create channel for %s: connection is not available", queue_name
            )
            raise RuntimeError("Connection is not available")

        logger.debug("Creating channel for queue %s", queue_name)
        channel = await self.connection.channel()
        await channel.set_qos(prefetch_count=self.config.prefetch_count)
        logger.debug("Created channel for queue %s", queue_name)
        return channel

    @asynccontextmanager
    async def create_channel(
        self, queue_name: str
    ) -> AsyncGenerator[aio_pika.abc.AbstractChannel, None]:
        """
        Create and yield a channel for the specified queue with retry mechanism.
        This context manager ensures the channel is properly managed.
        """
        try:
            # Create a new channel with retry
            channel = await retry_with_backoff(
                fn=lambda: self._establish_channel(queue_name),
                retry_policy=self.config.consumer_retry_policy,
                retry_exceptions=(
                    AMQPConnectionError,
                    AMQPChannelError,
                    ConnectionError,
                ),
            )

            # Save in the channels dict for tracking
            self.channels[queue_name] = channel
            logger.debug("Created new channel for queue %s", queue_name)

            try:
                yield channel
            finally:
                # Don't close the channel here as it might be used later
                # It will be closed during shutdown
                pass
        except aio_pika.exceptions.AMQPError as e:
            logger.error(
                "Error creating channel for queue %s after retries: %s", queue_name, e
            )
            raise

    async def _establish_connection(self) -> aio_pika.abc.AbstractConnection:
        """
        Creates a new RabbitMQ connection with retry logic.
        """
        try:
            logger.debug("Establishing connection to RabbitMQ")
            connection = await aio_pika.connect(
                self.config.url,
                heartbeat=self.config.connection_heartbeat_interval,
            )
            logger.debug("Connected to RabbitMQ successfully")
            return connection
        except Exception as e:
            logger.error("Failed to connect to RabbitMQ: %s", e)
            raise

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[aio_pika.abc.AbstractConnection, None]:
        """
        Create and manage the main connection to RabbitMQ with automatic retry.
        """
        if self.connection is not None and not self.connection.is_closed:
            logger.debug("Connection already exists, reusing existing connection")
            try:
                yield self.connection
            finally:
                # The existing connection will be handled by close_channels_and_connection
                pass
            return

        try:
            # Create a new connection with retry
            self.connection = await retry_with_backoff(
                self._establish_connection,
                retry_policy=self.config.connection_retry_config,
                retry_exceptions=(
                    AMQPConnectionError,
                    ConnectionError,
                    OSError,
                    TimeoutError,
                ),
            )

            try:
                yield self.connection
            finally:
                # Don't close the connection here; it will be closed in close_channels_and_connection
                pass
        except Exception as e:
            logger.error(
                "Failed to establish connection to RabbitMQ after retries: %s", e
            )
            if self.connection:
                try:
                    await self.connection.close()
                except Exception as close_error:
                    logger.error(
                        "Error closing connection after connect failure: %s",
                        close_error,
                    )
                self.connection = None
            raise

    @asynccontextmanager
    async def get_channel_ctx(
        self, queue_name: str
    ) -> AsyncGenerator[aio_pika.abc.AbstractChannel, None]:
        """
        Get a channel for a specific queue as a context manager.
        This is safer than using get_channel directly as it ensures proper error handling.
        """
        max_retries = 3
        retry_delay = 1.0

        for attempt in range(max_retries):
            try:
                channel = await self.get_channel(queue_name)
                if channel is not None:
                    try:
                        yield channel
                        return
                    finally:
                        # We don't close the channel here as it's managed by the consumer
                        pass

                # No channel available, check connection state
                if (
                    self.connection
                    and not self.connection.is_closed
                    and self.connection_healthy
                ):
                    # Try to create a new channel
                    async with self.create_channel(queue_name) as new_channel:
                        yield new_channel
                        return
                else:
                    # Connection is not healthy, trigger shutdown
                    logger.error(
                        "Connection not healthy while getting channel for %s, triggering shutdown",
                        queue_name,
                    )
                    self._trigger_shutdown()
                    raise RuntimeError(
                        f"Cannot get channel for queue {queue_name}: connection is not healthy"
                    )

            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Error getting channel for %s, retrying: %s", queue_name, e
                    )
                    await self._wait_delay_or_shutdown(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error(
                        "Failed to get channel for %s after %s attempts: %s",
                        queue_name,
                        max_retries,
                        e,
                    )
                    raise

    async def _wait_delay_or_shutdown(self, delay: float) -> None:
        """
        Wait for the specified delay or exit early if shutdown is initiated.

        Args:
            delay: Delay in seconds to wait
        """

        wait_cor = asyncio.create_task(asyncio.sleep(delay), name="delayed-retry-wait")
        wait_shutdown_cor = asyncio.create_task(
            self.shutdown_event.wait(), name="delayed-retry-shutdown-wait"
        )

        await asyncio.wait(
            [wait_cor, wait_shutdown_cor],
            return_when=asyncio.FIRST_COMPLETED,
        )

    async def _monitor_connection_health(self) -> None:
        """
        Monitor connection health and trigger shutdown if connection is lost.
        This runs as a background task.
        """
        while not self.shutdown_event.is_set():
            try:
                await self._wait_delay_or_shutdown(
                    self.config.connection_health_check_interval
                )

                if self.shutdown_event.is_set():
                    break

                # Check connection health
                if not await self._is_connection_healthy():
                    logger.error(
                        "Connection health check failed, initiating worker shutdown"
                    )
                    self.shutdown()
                    break

            except asyncio.CancelledError:
                logger.debug("Connection health monitoring cancelled")
                break
            except Exception as e:
                logger.error("Error in connection health monitoring: %s", e)
                await self._wait_delay_or_shutdown(5)  # Wait before retrying

    async def _is_connection_healthy(self) -> bool:
        """
        Check if the connection is healthy.
        """
        try:
            if self.connection is None or self.connection.is_closed:
                return False

            # Try to create a temporary channel to test connection
            async with self.connection.channel() as test_channel:
                # If we can create a channel, connection is healthy
                return True

        except Exception as e:
            logger.debug("Connection health check failed: %s", e)
            return False

    def _trigger_shutdown(self) -> None:
        """
        Trigger worker shutdown due to connection loss.
        """
        if not self.shutdown_event.is_set():
            logger.error("Connection lost, initiating worker shutdown")
            self.connection_healthy = False
            self.shutdown()

    async def _cleanup_connection(self) -> None:
        """
        Clean up existing connection and channels.
        """
        # Cancel existing consumers
        for queue_name, channel in self.channels.items():
            try:
                if not channel.is_closed:
                    # Cancel consumer if we have its tag
                    if queue_name in self.consumer_tags:
                        try:
                            queue = await channel.get_queue(queue_name, ensure=False)
                            if queue:
                                await queue.cancel(self.consumer_tags[queue_name])
                        except Exception as cancel_error:
                            logger.warning(
                                "Error cancelling consumer for %s: %s",
                                queue_name,
                                cancel_error,
                            )
                        del self.consumer_tags[queue_name]
            except Exception as e:
                logger.warning("Error cancelling consumer for %s: %s", queue_name, e)

        # Close channels
        for queue_name, channel in self.channels.items():
            try:
                if not channel.is_closed:
                    await channel.close()
            except Exception as e:
                logger.warning("Error closing channel for %s: %s", queue_name, e)

        self.channels.clear()

        # Close connection
        if self.connection and not self.connection.is_closed:
            try:
                await self.connection.close()
            except Exception as e:
                logger.warning("Error closing connection: %s", e)

        self.connection = None
        self.connection_healthy = False


def create_message_bus(
    broker_url: str,
    broker_backend: MessageBrokerBackend,
    scheduled_actions: SCHEDULED_ACTION_DATA_SET,
    message_handler_set: MESSAGE_HANDLER_DATA_SET,
    uow_context_provider: UnitOfWorkContextProvider,
) -> MessageBusConsumer:

    parsed_url = urlparse(broker_url)

    if parsed_url.scheme == "amqp" or parsed_url.scheme == "amqps":
        assert parsed_url.query, "Query string must be set for AMQP URLs"

        query_params: dict[str, list[str]] = parse_qs(parsed_url.query)

        assert "exchange" in query_params, "Exchange must be set in the query string"
        assert (
            len(query_params["exchange"]) == 1
        ), "Exchange must be set in the query string"
        assert (
            "prefetch_count" in query_params
        ), "Prefetch count must be set in the query string"
        assert (
            len(query_params["prefetch_count"]) == 1
        ), "Prefetch count must be set in the query string"
        assert query_params["prefetch_count"][
            0
        ].isdigit(), "Prefetch count must be an integer in the query string"
        assert query_params["exchange"][0], "Exchange must be set in the query string"
        assert query_params["prefetch_count"][
            0
        ], "Prefetch count must be set in the query string"

        exchange = query_params["exchange"][0]
        prefetch_count = int(query_params["prefetch_count"][0])

        # Parse optional retry configuration parameters
        connection_retry_config = RetryPolicy()
        consumer_retry_config = RetryPolicy(
            max_retries=5, initial_delay=5, max_delay=60.0, backoff_factor=3.0
        )

        # Parse heartbeat and health check intervals
        connection_heartbeat_interval = 30.0
        connection_health_check_interval = 10.0

        # Connection retry config parameters
        if (
            "connection_retry_max" in query_params
            and query_params["connection_retry_max"][0].isdigit()
        ):
            connection_retry_config.max_retries = int(
                query_params["connection_retry_max"][0]
            )

        if "connection_retry_delay" in query_params:
            try:
                connection_retry_config.initial_delay = float(
                    query_params["connection_retry_delay"][0]
                )
            except ValueError:
                pass

        if "connection_retry_max_delay" in query_params:
            try:
                connection_retry_config.max_delay = float(
                    query_params["connection_retry_max_delay"][0]
                )
            except ValueError:
                pass

        if "connection_retry_backoff" in query_params:
            try:
                connection_retry_config.backoff_factor = float(
                    query_params["connection_retry_backoff"][0]
                )
            except ValueError:
                pass

        # Consumer retry config parameters
        if (
            "consumer_retry_max" in query_params
            and query_params["consumer_retry_max"][0].isdigit()
        ):
            consumer_retry_config.max_retries = int(
                query_params["consumer_retry_max"][0]
            )

        if "consumer_retry_delay" in query_params:
            try:
                consumer_retry_config.initial_delay = float(
                    query_params["consumer_retry_delay"][0]
                )
            except ValueError:
                pass

        if "consumer_retry_max_delay" in query_params:
            try:
                consumer_retry_config.max_delay = float(
                    query_params["consumer_retry_max_delay"][0]
                )
            except ValueError:
                pass

        if "consumer_retry_backoff" in query_params:
            try:
                consumer_retry_config.backoff_factor = float(
                    query_params["consumer_retry_backoff"][0]
                )
            except ValueError:
                pass

        # Heartbeat and health check intervals
        if "connection_heartbeat_interval" in query_params:
            try:
                connection_heartbeat_interval = float(
                    query_params["connection_heartbeat_interval"][0]
                )
            except ValueError:
                pass

        if "connection_health_check_interval" in query_params:
            try:
                connection_health_check_interval = float(
                    query_params["connection_health_check_interval"][0]
                )
            except ValueError:
                pass

        config = AioPikaWorkerConfig(
            url=broker_url,
            exchange=exchange,
            prefetch_count=prefetch_count,
            connection_retry_config=connection_retry_config,
            consumer_retry_policy=consumer_retry_config,
            connection_heartbeat_interval=connection_heartbeat_interval,
            connection_health_check_interval=connection_health_check_interval,
        )

        return AioPikaMicroserviceConsumer(
            config=config,
            broker_backend=broker_backend,
            message_handler_set=message_handler_set,
            scheduled_actions=scheduled_actions,
            uow_context_provider=uow_context_provider,
        )

    raise ValueError(
        f"Unsupported broker URL scheme: {parsed_url.scheme}. Supported schemes are amqp and amqps"
    )


class ScheduledMessageHandlerCallback:
    def __init__(
        self,
        consumer: AioPikaMicroserviceConsumer,
        queue_name: str,
        routing_key: str,
        scheduled_action: ScheduledActionData,
    ):
        self.consumer = consumer
        self.queue_name = queue_name
        self.routing_key = routing_key
        self.scheduled_action = scheduled_action

    async def __call__(
        self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage
    ) -> None:

        if self.consumer.shutdown_event.is_set():
            logger.debug(
                "Shutdown in progress. Requeuing scheduled message for %s",
                self.queue_name,
            )
            try:
                # Use channel context for requeuing
                await aio_pika_message.reject(requeue=True)
            except RuntimeError:
                logger.warning(
                    "Could not requeue scheduled message during shutdown - channel not available"
                )
            except Exception as e:
                logger.error(
                    "Failed to requeue scheduled message during shutdown: %s", e
                )
            return

        # Check if connection is healthy before processing
        if not self.consumer.connection_healthy:
            logger.warning(
                "Connection not healthy, requeuing scheduled message for %s",
                self.queue_name,
            )
            try:
                if not self.consumer.connection_healthy:
                    # Still not healthy, requeue the message

                    await aio_pika_message.reject(requeue=True)
                    return
            except Exception as e:
                logger.error(
                    "Failed to requeue scheduled message due to connection issues: %s",
                    e,
                )
                return

        async with self.consumer.lock:
            task = asyncio.create_task(
                self.handle_message(aio_pika_message),
                name=f"ScheduledAction-{self.queue_name}-handle-message-{aio_pika_message.message_id}",
            )
            self.consumer.tasks.add(task)
            task.add_done_callback(self.handle_message_consume_done)

    def handle_message_consume_done(self, task: asyncio.Task[Any]) -> None:
        self.consumer.tasks.discard(task)
        if task.cancelled():
            logger.warning("Scheduled task for %s was cancelled", self.queue_name)
            return

        if (error := task.exception()) is not None:
            logger.exception(
                "Error processing scheduled action %s", self.queue_name, exc_info=error
            )

    async def handle_message(
        self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage
    ) -> None:

        if self.consumer.shutdown_event.is_set():
            logger.debug(
                "Shutdown event set. Requeuing message for %s", self.queue_name
            )
            try:
                # Use channel context for requeuing

                await aio_pika_message.reject(requeue=True)
                return
            except RuntimeError:
                logger.warning(
                    "Could not requeue message during shutdown - channel not available"
                )
            except Exception as e:
                logger.error("Failed to requeue message during shutdown: %s", e)
                return

        # Check connection health before processing
        if not self.consumer.connection_healthy:
            logger.warning(
                "Connection not healthy, requeuing scheduled message for %s",
                self.queue_name,
            )
            try:

                await aio_pika_message.reject(requeue=True)
                return
            except Exception as e:
                logger.error(
                    "Failed to requeue scheduled message due to connection issues: %s",
                    e,
                )
                return

        sig = inspect.signature(self.scheduled_action.callable)
        if len(sig.parameters) == 1:

            task = asyncio.create_task(
                self.run_with_context(
                    self.scheduled_action,
                    (ScheduleDispatchData(int(aio_pika_message.body.decode("utf-8"))),),
                    {},
                ),
                name=f"ScheduledAction-{self.queue_name}-handle-message-{aio_pika_message.message_id}",
            )

        elif len(sig.parameters) == 0:
            task = asyncio.create_task(
                self.run_with_context(
                    self.scheduled_action,
                    (),
                    {},
                ),
                name=f"ScheduledAction-{self.queue_name}-handle-message-{aio_pika_message.message_id}",
            )
        else:
            logger.warning(
                "Scheduled action '%s' must have exactly one parameter of type ScheduleDispatchData or no parameters"
                % self.queue_name
            )
            return

        self.consumer.tasks.add(task)
        task.add_done_callback(self.handle_message_consume_done)

        try:
            await task
        except Exception as e:

            logger.exception(
                "Error processing scheduled action %s: %s", self.queue_name, e
            )

    async def run_with_context(
        self,
        scheduled_action: ScheduledActionData,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:

        with provide_shutdown_state(self.consumer.shutdown_state):
            async with self.consumer.uow_context_provider(
                AppTransactionContext(
                    controller_member_reflect=scheduled_action.controller_member,
                    transaction_data=SchedulerTransactionData(
                        task_name=scheduled_action.spec.name
                        or scheduled_action.callable.__qualname__,
                        scheduled_to=datetime.now(UTC),
                        cron_expression=scheduled_action.spec.cron,
                        triggered_at=datetime.now(UTC),
                    ),
                )
            ):

                await scheduled_action.callable(*args, **kwargs)


class MessageHandlerCallback:

    def __init__(
        self,
        consumer: AioPikaMicroserviceConsumer,
        queue_name: str,
        routing_key: str,
        message_handler: MessageHandlerData,
    ):
        self.consumer = consumer
        self.queue_name = queue_name
        self.routing_key = routing_key
        self.message_handler = message_handler
        self.retry_state: dict[str, dict[str, Any]] = {}

    async def message_consumer(
        self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage
    ) -> None:
        if self.consumer.shutdown_event.is_set():
            logger.debug(
                "Shutdown in progress. Requeuing message for %s", self.queue_name
            )
            try:
                # Use channel context for requeuing

                await aio_pika_message.reject(requeue=True)
            except RuntimeError:
                logger.warning(
                    "Could not requeue message during shutdown - channel not available"
                )
            except Exception as e:
                logger.error("Failed to requeue message during shutdown: %s", e)
            return

        # Check if connection is healthy before processing
        if not self.consumer.connection_healthy:
            logger.warning(
                "Connection not healthy, requeuing message for %s", self.queue_name
            )
            try:
                if not self.consumer.connection_healthy:
                    # Still not healthy, requeue the message

                    await aio_pika_message.reject(requeue=True)
                    return
            except Exception as e:
                logger.error(
                    "Failed to requeue message due to connection issues: %s", e
                )
                return

        async with self.consumer.lock:
            task = asyncio.create_task(
                self.handle_message(aio_pika_message),
                name=f"MessageHandler-{self.queue_name}-handle-message-{aio_pika_message.message_id}",
            )
            self.consumer.tasks.add(task)

            def handle_message_consume_done(task: asyncio.Task[Any]) -> None:
                self.consumer.tasks.discard(task)
                if task.cancelled():
                    logger.warning("Task for queue %s was cancelled", self.queue_name)
                    return

                if (error := task.exception()) is not None:
                    logger.exception(
                        "Error processing message id %s for queue %s",
                        aio_pika_message.message_id,
                        self.queue_name,
                        exc_info=error,
                    )

            task.add_done_callback(handle_message_consume_done)

    async def __call__(
        self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage
    ) -> None:
        await self.message_consumer(aio_pika_message)

    async def handle_reject_message(
        self,
        aio_pika_message: aio_pika.abc.AbstractIncomingMessage,
        *,
        requeue_timeout: float,
        requeue: bool = False,
        retry_count: int = 0,
        exception: Optional[BaseException] = None,
    ) -> None:
        """
        Handle rejecting a message, with support for retry with exponential backoff.

        Args:
            aio_pika_message: The message to reject
            requeue: Whether to requeue the message directly (True) or handle with retry logic (False)
            retry_count: The current retry count for this message
            exception: The exception that caused the rejection, if any
        """
        message_id = aio_pika_message.message_id or str(uuid.uuid4())

        try:
            # Check if we should retry with backoff
            if (
                not requeue
                and self.message_handler.spec.nack_on_exception
                and exception is not None
            ):
                # Get retry config from consumer
                retry_config = (
                    self.message_handler.spec.retry_config
                    or self.consumer.config.consumer_retry_policy
                )

                # Check if we reached max retries
                if retry_count >= retry_config.max_retries:
                    logger.warning(
                        "Message %s (%s) failed after %s retries, dead-lettering: %s",
                        message_id,
                        self.queue_name,
                        retry_count,
                        str(exception),
                    )
                    # Dead-letter the message after max retries
                    try:
                        await aio_pika_message.reject(requeue=False)
                    except Exception as e:
                        logger.error(
                            "Failed to dead-letter message %s: %s", message_id, e
                        )
                    return

                # Calculate delay for this retry attempt
                delay = retry_config.initial_delay * (
                    retry_config.backoff_factor**retry_count
                )
                if retry_config.jitter:
                    jitter_amount = delay * 0.25
                    delay = delay + random.uniform(-jitter_amount, jitter_amount)
                    delay = max(
                        delay, 0.1
                    )  # Ensure delay doesn't go negative due to jitter

                delay = min(delay, retry_config.max_delay)

                logger.warning(
                    "Message %s (%s) failed with %s, retry %s/%s scheduled in %.2fs",
                    message_id,
                    self.queue_name,
                    str(exception),
                    retry_count + 1,
                    retry_config.max_retries,
                    delay,
                )

                # Store retry state for this message
                self.retry_state[message_id] = {
                    "retry_count": retry_count + 1,
                    "last_exception": exception,
                    "next_retry": time.time() + delay,
                }

                # Schedule retry after delay
                task = asyncio.create_task(
                    self._delayed_retry(
                        aio_pika_message, delay, retry_count + 1, exception
                    ),
                    name=f"MessageHandler-{self.queue_name}-delayed-retry-{message_id}",
                )
                self.consumer.tasks.add(task)

                # Acknowledge the current message since we'll handle retry ourselves
                try:
                    await aio_pika_message.ack()
                except Exception as e:
                    logger.error(
                        "Failed to acknowledge message %s for retry: %s", message_id, e
                    )
                return

            # Standard reject without retry or with immediate requeue
            try:
                await self._wait_delay_or_shutdown(
                    requeue_timeout
                )  # Optional delay before requeueing
                await aio_pika_message.reject(requeue=requeue)
                if requeue:
                    logger.warning(
                        "Message %s (%s) requeued for immediate retry",
                        message_id,
                        self.queue_name,
                    )
                else:
                    logger.warning(
                        "Message %s (%s) rejected without requeue",
                        message_id,
                        self.queue_name,
                    )
            except Exception as e:
                logger.error("Failed to reject message %s: %s", message_id, e)

        except Exception as e:
            logger.exception(
                "Unexpected error in handle_reject_message for %s (%s): %s",
                message_id,
                self.queue_name,
                e,
            )

    async def _delayed_retry(
        self,
        aio_pika_message: aio_pika.abc.AbstractIncomingMessage,
        delay: float,
        retry_count: int,
        exception: Optional[BaseException],
    ) -> None:
        """
        Handle delayed retry of a message after exponential backoff delay.

        Args:
            aio_pika_message: The original message
            delay: Delay in seconds before retrying
            retry_count: The current retry count (after increment)
            exception: The exception that caused the failure
        """
        message_id = aio_pika_message.message_id or str(uuid.uuid4())

        try:
            # Wait for the backoff delay
            await self._wait_delay_or_shutdown(delay)

            # Get message body and properties for republishing
            message_body = aio_pika_message.body
            headers = (
                aio_pika_message.headers.copy() if aio_pika_message.headers else {}
            )

            # Add retry information to headers
            headers["x-retry-count"] = retry_count
            if exception:
                headers["x-last-error"] = str(exception)

            # Clean up retry state
            if message_id in self.retry_state:
                del self.retry_state[message_id]

            # Republish the message to the same queue with retry logic
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    async with self.consumer.get_channel_ctx(
                        self.queue_name
                    ) as channel:
                        exchange = await RabbitmqUtils.get_main_exchange(
                            channel=channel,
                            exchange_name=self.consumer.config.exchange,
                        )

                        await exchange.publish(
                            aio_pika.Message(
                                body=message_body,
                                headers=headers,
                                message_id=message_id,
                                content_type=aio_pika_message.content_type,
                                content_encoding=aio_pika_message.content_encoding,
                                delivery_mode=aio_pika_message.delivery_mode,
                            ),
                            routing_key=self.routing_key,
                        )

                        logger.warning(
                            "Message %s (%s) republished for retry %s",
                            message_id,
                            self.queue_name,
                            retry_count,
                        )
                        return

                except Exception as e:
                    if attempt < max_attempts - 1:
                        logger.warning(
                            "Failed to republish message %s (attempt %s): %s",
                            message_id,
                            attempt + 1,
                            e,
                        )
                        await self._wait_delay_or_shutdown(
                            (1.0 * (attempt + 1))
                        )  # Exponential backoff
                    else:
                        logger.error(
                            "Failed to republish message %s after %s attempts: %s",
                            message_id,
                            max_attempts,
                            e,
                        )
                        raise

        except Exception as e:
            logger.exception(
                "Failed to execute delayed retry for message %s (%s): %s",
                message_id,
                self.queue_name,
                e,
            )
            # If we fail to republish, try to dead-letter the original message
            try:
                if message_id in self.retry_state:
                    del self.retry_state[message_id]
            except Exception:
                pass

    async def _wait_delay_or_shutdown(self, delay: float) -> None:
        await self.consumer._wait_delay_or_shutdown(delay)

    async def handle_message(
        self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage
    ) -> None:

        routing_key = self.queue_name

        if routing_key is None:
            logger.warning("No topic found for message")
            await self.handle_reject_message(aio_pika_message)
            return

        handler_data = self.message_handler

        handler = handler_data.instance_callable
        handler_method = handler_data.controller_member.member_function

        # sig = inspect.signature(handler)

        # if len(sig.parameters) != 1:
        #     logger.warning(
        #         "Handler for topic '%s' must have exactly one parameter which is MessageOf[T extends Message]"
        #         % routing_key
        #     )
        #     return

        # parameter = list(sig.parameters.values())[0]

        # param_origin = get_origin(parameter.annotation)

        # if param_origin is not MessageOf:
        #     logger.warning(
        #         "Handler for topic '%s' must have exactly one parameter of type Message"
        #         % routing_key
        #     )
        #     return

        # if len(parameter.annotation.__args__) != 1:
        #     logger.warning(
        #         "Handler for topic '%s' must have exactly one parameter of type Message"
        #         % routing_key
        #     )
        #     return

        # message_type = parameter.annotation.__args__[0]

        # if not issubclass(message_type, BaseModel):
        #     logger.warning(
        #         "Handler for topic '%s' must have exactly one parameter of type MessageOf[BaseModel]"
        #         % routing_key
        #     )
        #     return

        mode, message_type = MessageHandler.validate_decorated_fn(handler_method)

        built_message = AioPikaMessage(aio_pika_message, message_type)

        incoming_message_spec = MessageHandler.get_last(handler)
        assert incoming_message_spec is not None, "Incoming message spec must be set"
        # Extract retry count from headers if available
        headers = aio_pika_message.headers or {}
        retry_count = int(str(headers.get("x-retry-count", 0)))

        with provide_implicit_headers(aio_pika_message.headers), provide_shutdown_state(
            self.consumer.shutdown_state
        ):
            async with self.consumer.uow_context_provider(
                AppTransactionContext(
                    controller_member_reflect=handler_data.controller_member,
                    transaction_data=MessageBusTransactionData(
                        message_id=aio_pika_message.message_id,
                        processing_attempt=retry_count + 1,
                        message_type=message_type,
                        message=built_message,
                        topic=routing_key,
                    ),
                )
            ):
                maybe_timeout_ctx: AsyncContextManager[Any]
                if incoming_message_spec.timeout is not None:
                    maybe_timeout_ctx = asyncio.timeout(incoming_message_spec.timeout)
                else:
                    maybe_timeout_ctx = none_context()

                start_time = time.perf_counter()
                async with maybe_timeout_ctx:
                    try:
                        with provide_bus_message_controller(
                            AioPikaMessageBusController(aio_pika_message)
                        ):
                            try:
                                if mode == "WRAPPED":
                                    future = handler(built_message)
                                else:
                                    try:

                                        payload = built_message.payload()
                                    except ValidationError as exc:
                                        logger.exception(
                                            "Validation error parsing message %s on topic %s",
                                            aio_pika_message.message_id or "unknown",
                                            routing_key,
                                        )
                                        aio_pika_message.headers["x-last-error"] = (
                                            "Validation error parsing message payload"
                                        )
                                        await aio_pika_message.reject(requeue=False)
                                        record_exception(
                                            exc,
                                        )
                                        set_span_status("ERROR")
                                        return
                                    future = handler(payload)

                                await future

                                with suppress(aio_pika.MessageProcessError):
                                    # Use channel context for acknowledgement with retry
                                    try:
                                        await aio_pika_message.ack()
                                        set_span_status("OK")
                                    except Exception as ack_error:
                                        logger.warning(
                                            "Failed to acknowledge message %s: %s",
                                            aio_pika_message.message_id or "unknown",
                                            ack_error,
                                        )
                                successfully = True
                            except Exception as base_exc:
                                set_span_status("ERROR")
                                record_exception(
                                    base_exc,
                                    {
                                        "message_id": aio_pika_message.message_id
                                        or "unknown",
                                        "routing_key": routing_key,
                                    },
                                )
                                successfully = False
                                # Get message id for logging
                                message_id = aio_pika_message.message_id or "unknown"

                                # Process exception handler if configured
                                if incoming_message_spec.exception_handler is not None:
                                    try:
                                        incoming_message_spec.exception_handler(
                                            base_exc
                                        )
                                    except Exception as nested_exc:
                                        logger.exception(
                                            "Error processing exception handler for message %s: %s | %s",
                                            message_id,
                                            base_exc,
                                            nested_exc,
                                        )
                                else:
                                    logger.exception(
                                        "Error processing message %s on topic %s: %s",
                                        message_id,
                                        routing_key,
                                        str(base_exc),
                                    )

                                # Handle rejection with retry logic
                                if incoming_message_spec.nack_on_exception:
                                    await self.handle_reject_message(
                                        aio_pika_message,
                                        requeue_timeout=incoming_message_spec.nack_delay_on_exception,
                                        requeue=False,  # Don't requeue directly, use our backoff mechanism
                                        retry_count=retry_count,
                                        exception=base_exc,
                                    )
                                else:
                                    # Message shouldn't be retried, reject it
                                    await self.handle_reject_message(
                                        aio_pika_message,
                                        requeue=False,
                                        requeue_timeout=incoming_message_spec.nack_delay_on_exception,
                                        exception=base_exc,
                                    )

                            elapsed_time = time.perf_counter() - start_time
                            # Message processed successfully, log and clean up any retry state
                            message_id = aio_pika_message.message_id or str(
                                uuid.uuid4()
                            )
                            if message_id in self.retry_state:
                                del self.retry_state[message_id]

                            # Log success with retry information if applicable
                            headers = aio_pika_message.headers or {}
                            traceparent = headers.get("traceparent")
                            trace_info = (
                                f" [traceparent={str(traceparent)}]"
                                if traceparent
                                else ""
                            )

                            if "x-retry-count" in headers:
                                retry_count = int(str(headers.get("x-retry-count", 0)))
                                logger.debug(
                                    "Message %s#%s processed "
                                    + (
                                        "successfully"
                                        if successfully
                                        else "with errors"
                                    )
                                    + " after %s retries in %.4fs%s",
                                    message_id,
                                    self.queue_name,
                                    retry_count,
                                    elapsed_time,
                                    trace_info,
                                )
                            else:
                                logger.debug(
                                    "Message %s#%s processed "
                                    + (
                                        "successfully"
                                        if successfully
                                        else "with errors"
                                    )
                                    + " in %.4fs%s",
                                    message_id,
                                    self.queue_name,
                                    elapsed_time,
                                    trace_info,
                                )
                                ...

                    except Exception as base_exc:
                        logger.critical(
                            f"Critical error processing message {aio_pika_message.message_id} when providing bus message controller: {base_exc}"
                        )


@asynccontextmanager
async def none_context() -> AsyncGenerator[None, None]:
    yield


class MessageBusWorker:
    def __init__(
        self,
        app: Microservice,
        broker_url: str,
        backend_url: str,
        handler_names: set[str] | None = None,
    ) -> None:
        self.app = app
        self.backend_url = backend_url
        self.broker_url = broker_url
        self.handler_names = handler_names

        self.container = Container(app)
        self.lifecycle = AppLifecycle(app, self.container)

        self.uow_context_provider = UnitOfWorkContextProvider(
            app=app, container=self.container
        )

        self._consumer: MessageBusConsumer | None = None

    @property
    def consumer(self) -> MessageBusConsumer:
        if self._consumer is None:
            raise RuntimeError("Consumer not started")
        return self._consumer

    async def start_async(self) -> None:
        all_message_handlers_set: MESSAGE_HANDLER_DATA_SET = set()
        all_scheduled_actions_set: SCHEDULED_ACTION_DATA_SET = set()
        with providing_app_type("worker"):
            async with self.lifecycle():
                for instance_class in self.app.controllers:
                    controller = MessageBusController.get_last(instance_class)

                    if controller is None:
                        continue

                    instance: Any = self.container.get_by_type(instance_class)

                    factory = controller.get_messagebus_factory()
                    handlers, schedulers = factory(instance)

                    message_handler_data_map: dict[str, MessageHandlerData] = {}
                    all_scheduled_actions_set.update(schedulers)
                    for handler_data in handlers:
                        message_type = handler_data.spec.message_type
                        topic = message_type.MESSAGE_TOPIC

                        # Filter handlers by name if specified
                        if (
                            self.handler_names is not None
                            and handler_data.spec.name is not None
                        ):
                            if handler_data.spec.name not in self.handler_names:
                                continue
                        elif (
                            self.handler_names is not None
                            and handler_data.spec.name is None
                        ):
                            # Skip handlers without names when filtering is requested
                            continue

                        if (
                            topic in message_handler_data_map
                            and message_type.MESSAGE_TYPE == "task"
                        ):
                            logger.warning(
                                "Task handler for topic '%s' already registered. Skipping"
                                % topic
                            )
                            continue
                        message_handler_data_map[topic] = handler_data
                        all_message_handlers_set.add(handler_data)

                broker_backend = get_message_broker_backend_from_url(
                    url=self.backend_url
                )

                consumer = self._consumer = create_message_bus(
                    broker_url=self.broker_url,
                    broker_backend=broker_backend,
                    scheduled_actions=all_scheduled_actions_set,
                    message_handler_set=all_message_handlers_set,
                    uow_context_provider=self.uow_context_provider,
                )

                await consumer.consume()

    def start_sync(self) -> None:

        def on_shutdown(loop: asyncio.AbstractEventLoop) -> None:
            logger.warning("Shutting down - signal received")
            # Schedule the shutdown to run in the event loop
            asyncio.create_task(
                self._graceful_shutdown(), name="Worker-Graceful-Shutdown"
            )
            # wait until the shutdown is complete

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            loop = runner.get_loop()
            loop.add_signal_handler(signal.SIGINT, on_shutdown, loop)
            # Add graceful shutdown handler for SIGTERM as well
            loop.add_signal_handler(signal.SIGTERM, on_shutdown, loop)
            try:
                runner.run(self.start_async())
            except Exception as e:
                logger.critical("Worker failed to start due to connection error: %s", e)
                # Exit with error code 1 to indicate startup failure
                import sys

                sys.exit(1)

    async def _graceful_shutdown(self) -> None:
        """Handles graceful shutdown process"""
        logger.warning("Initiating graceful shutdown sequence")
        # Use the comprehensive close method that handles shutdown, task waiting and connection cleanup

        self.consumer.shutdown()
        logger.warning("Graceful shutdown completed")


class AioPikaMessageBusController(BusMessageController):
    def __init__(self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage):
        self.aio_pika_message = aio_pika_message
        # We access consumer callback through context if available
        self._callback: Optional[MessageHandlerCallback] = None

    def _get_callback(self) -> MessageHandlerCallback:
        """
        Find the callback associated with this message.
        This allows us to access the retry mechanisms.
        """
        if self._callback is None:
            # Get the context from current frame's locals
            frame = inspect.currentframe()
            if frame is not None:
                try:
                    caller_frame = frame.f_back
                    if caller_frame is not None:
                        # Check for context with handler callback
                        callback_ref = None
                        # Look for handler_message call context
                        while caller_frame is not None:
                            if "self" in caller_frame.f_locals:
                                self_obj = caller_frame.f_locals["self"]
                                if isinstance(self_obj, MessageHandlerCallback):
                                    callback_ref = self_obj
                                    break
                            caller_frame = caller_frame.f_back
                        # Save callback reference if we found it
                        self._callback = callback_ref
                finally:
                    del frame  # Avoid reference cycles

            if self._callback is None:
                raise RuntimeError("Could not find callback context for message retry")

        return self._callback

    async def ack(self) -> None:
        await self.aio_pika_message.ack()

    async def nack(self) -> None:
        await self.aio_pika_message.nack()

    async def reject(self) -> None:
        await self.aio_pika_message.reject()

    async def retry(self, delay: float = 5) -> None:
        """
        Retry the message immediately by rejecting with requeue flag.
        This doesn't use the exponential backoff mechanism.
        """
        callback = self._get_callback()
        await callback.handle_reject_message(
            self.aio_pika_message, requeue=True, requeue_timeout=delay
        )

    async def retry_later(self, delay: int) -> None:
        """
        Retry the message after a specified delay using the exponential backoff mechanism.

        Args:
            delay: Minimum delay in seconds before retrying
        """
        try:
            callback = self._get_callback()

            # Get current retry count from message headers
            headers = self.aio_pika_message.headers or {}
            retry_count = int(str(headers.get("x-retry-count", 0)))

            # Handle retry with explicit delay
            asyncio.create_task(
                callback._delayed_retry(
                    self.aio_pika_message,
                    float(delay),
                    retry_count + 1,
                    None,  # No specific exception
                ),
                name=f"MessageHandler-{callback.queue_name}-delayed-retry-{self.aio_pika_message.message_id or 'unknown'}-{int(time.time())}",
            )

            # Acknowledge the current message since we'll republish
            await self.aio_pika_message.ack()

        except Exception as e:
            logger.exception("Failed to schedule retry_later: %s", e)
            # Fall back to immediate retry
            await self.aio_pika_message.reject(requeue=True)
