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
from typing import (
    Any,
    AsyncContextManager,
    AsyncGenerator,
    Awaitable,
    Optional,
    Type,
    get_origin,
)
from urllib.parse import parse_qs, urlparse

import aio_pika
import aio_pika.abc
import uvloop
from aio_pika.exceptions import AMQPError, ChannelClosed, ChannelNotFoundEntity
from pydantic import BaseModel

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
from jararaca.messagebus.message import Message, MessageOf
from jararaca.microservice import (
    AppTransactionContext,
    MessageBusTransactionData,
    Microservice,
    SchedulerTransactionData,
    ShutdownState,
    provide_shutdown_state,
)
from jararaca.scheduler.decorators import ScheduledActionData
from jararaca.utils.rabbitmq_utils import RabbitmqUtils
from jararaca.utils.retry import RetryConfig, retry_with_backoff

logger = logging.getLogger(__name__)


@dataclass
class AioPikaWorkerConfig:
    url: str
    exchange: str
    prefetch_count: int
    connection_retry_config: RetryConfig = field(
        default_factory=lambda: RetryConfig(
            max_retries=15,
            initial_delay=1.0,
            max_delay=60.0,
            backoff_factor=2.0,
        )
    )
    consumer_retry_config: RetryConfig = field(
        default_factory=lambda: RetryConfig(
            max_retries=15,
            initial_delay=0.5,
            max_delay=40.0,
            backoff_factor=2.0,
        )
    )


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
                queue = await RabbitmqUtils.get_queue(
                    channel=channel, queue_name=queue_name
                )

                # Configure consumer right away while in the context
                await queue.consume(
                    callback=MessageHandlerCallback(
                        consumer=self,
                        queue_name=queue_name,
                        routing_key=routing_key,
                        message_handler=handler,
                    ),
                    no_ack=handler.spec.auto_ack,
                )

                logger.info(
                    f"Consuming message handler {queue_name} on dedicated channel"
                )

        try:
            # Setup with retry
            await retry_with_backoff(
                setup_consumer,
                retry_config=self.config.consumer_retry_config,
                retry_exceptions=(ChannelNotFoundEntity, ChannelClosed, AMQPError),
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

                # Configure consumer right away while in the context
                await queue.consume(
                    callback=ScheduledMessageHandlerCallback(
                        consumer=self,
                        queue_name=queue_name,
                        routing_key=routing_key,
                        scheduled_action=scheduled_action,
                    ),
                    no_ack=True,
                )

                logger.info(f"Consuming scheduler {queue_name} on dedicated channel")

        try:
            # Setup with retry
            await retry_with_backoff(
                setup_consumer,
                retry_config=self.config.consumer_retry_config,
                retry_exceptions=(ChannelNotFoundEntity, ChannelClosed, AMQPError),
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
        # Verify infrastructure with retry
        infra_check_success = await retry_with_backoff(
            self._verify_infrastructure,
            retry_config=self.config.connection_retry_config,
            retry_exceptions=(Exception,),
        )

        if not infra_check_success:
            logger.critical("Failed to verify RabbitMQ infrastructure. Shutting down.")
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
                    )
                )
            )
            # task.add_done_callback(tasks.discard)
            # success = await self._setup_message_handler_consumer(handler)
            # if not success:
            #     logger.warning(
            #         f"Failed to set up consumer for {queue_name}, will not process messages from this queue"
            #     )

        # Setup scheduled actions
        for scheduled_action in self.scheduled_actions:

            queue_name = f"{scheduled_action.callable.__module__}.{scheduled_action.callable.__qualname__}"
            tasks.add(
                task := asyncio.create_task(
                    wait_for(
                        "scheduled_action",
                        queue_name,
                        self._setup_scheduled_action_consumer(scheduled_action),
                    )
                )
            )
            # task.add_done_callback(tasks.discard)

            # success = await self._setup_scheduled_action_consumer(scheduled_action)
            # if not success:
            #     queue_name = f"{scheduled_action.callable.__module__}.{scheduled_action.callable.__qualname__}"
            #     logger.warning(
            #         f"Failed to set up consumer for scheduled action {queue_name}, will not process scheduled tasks from this queue"
            #     )

        async def handle_task_results() -> None:
            for task in asyncio.as_completed(tasks):
                type, name, success = await task
                if success:
                    logger.info(f"Successfully set up {type} consumer for {name}")
                else:
                    logger.warning(
                        f"Failed to set up {type} consumer for {name}, will not process messages from this queue"
                    )

        handle_task_results_task = asyncio.create_task(handle_task_results())

        # Wait for shutdown signal
        await self.shutdown_event.wait()
        logger.info("Shutdown event received, stopping consumers")
        handle_task_results_task.cancel()
        with suppress(asyncio.CancelledError):
            await handle_task_results_task
        for task in tasks:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        logger.info("Worker shutting down")

        # Wait for all tasks to complete
        await self.wait_all_tasks_done()

        # Close all channels and the connection
        await self.close_channels_and_connection()

    async def wait_all_tasks_done(self) -> None:
        if not self.tasks:
            return

        logger.info(f"Waiting for {len(self.tasks)} in-flight tasks to complete")
        async with self.lock:
            # Use gather with return_exceptions=True to ensure all tasks are awaited
            # even if some raise exceptions
            results = await asyncio.gather(*self.tasks, return_exceptions=True)

            # Log any exceptions that occurred
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Task raised an exception during shutdown: {result}")

    async def close_channels_and_connection(self) -> None:
        """Close all channels and then the connection"""
        # Close all channels
        channel_close_tasks = []
        for queue_name, channel in self.channels.items():
            try:
                if not channel.is_closed:
                    logger.info(f"Closing channel for queue {queue_name}")
                    channel_close_tasks.append(channel.close())
                else:
                    logger.info(f"Channel for queue {queue_name} already closed")
            except Exception as e:
                logger.error(
                    f"Error preparing to close channel for queue {queue_name}: {e}"
                )

        # Wait for all channels to close (if any)
        if channel_close_tasks:
            try:
                await asyncio.gather(*channel_close_tasks, return_exceptions=True)
            except Exception as e:
                logger.error(f"Error during channel closures: {e}")

        # Clear channels dictionary
        self.channels.clear()

        # Close the connection
        if self.connection:
            try:
                if not self.connection.is_closed:
                    logger.info("Closing RabbitMQ connection")
                    await self.connection.close()
                else:
                    logger.info("RabbitMQ connection already closed")
            except Exception as e:
                logger.error(f"Error closing RabbitMQ connection: {e}")
            self.connection = None

    def shutdown(self) -> None:
        """Signal for shutdown"""
        logger.info("Initiating graceful shutdown")
        self.shutdown_event.set()

    async def close(self) -> None:
        """Implement MessageBusConsumer.close for cleanup"""
        self.shutdown()
        await self.wait_all_tasks_done()
        await self.close_channels_and_connection()

    async def get_channel(self, queue_name: str) -> aio_pika.abc.AbstractChannel | None:
        """
        Get the channel for a specific queue, or None if not found.
        This helps with error handling when a channel might have been closed.
        """
        if queue_name not in self.channels:
            logger.warning(f"No channel found for queue {queue_name}")
            return None

        try:
            channel = self.channels[queue_name]
            if channel.is_closed:
                logger.warning(f"Channel for queue {queue_name} is closed")
                # Attempt to recreate the channel if needed
                if self.connection and not self.connection.is_closed:
                    logger.info(f"Creating new channel for {queue_name}")
                    self.channels[queue_name] = await self.connection.channel()
                    await self.channels[queue_name].set_qos(
                        prefetch_count=self.config.prefetch_count
                    )
                    return self.channels[queue_name]
                return None
            return channel
        except Exception as e:
            logger.error(f"Error accessing channel for queue {queue_name}: {e}")
            return None

    async def _establish_channel(self, queue_name: str) -> aio_pika.abc.AbstractChannel:
        """
        Creates a new channel for the specified queue with proper QoS settings.
        """
        if self.connection is None or self.connection.is_closed:
            logger.warning(
                f"Cannot create channel for {queue_name}: connection is not available"
            )
            raise RuntimeError("Connection is not available")

        logger.debug(f"Creating channel for queue {queue_name}")
        channel = await self.connection.channel()
        await channel.set_qos(prefetch_count=self.config.prefetch_count)
        logger.debug(f"Created channel for queue {queue_name}")
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
                retry_config=self.config.consumer_retry_config,
                retry_exceptions=(
                    aio_pika.exceptions.AMQPConnectionError,
                    aio_pika.exceptions.AMQPChannelError,
                    ConnectionError,
                ),
            )

            # Save in the channels dict for tracking
            self.channels[queue_name] = channel
            logger.debug(f"Created new channel for queue {queue_name}")

            try:
                yield channel
            finally:
                # Don't close the channel here as it might be used later
                # It will be closed during shutdown
                pass
        except aio_pika.exceptions.AMQPError as e:
            logger.error(
                f"Error creating channel for queue {queue_name} after retries: {e}"
            )
            raise

    async def _establish_connection(self) -> aio_pika.abc.AbstractConnection:
        """
        Creates a new RabbitMQ connection with retry logic.
        """
        try:
            logger.info("Establishing connection to RabbitMQ")
            connection = await aio_pika.connect(self.config.url)
            logger.info("Connected to RabbitMQ successfully")
            return connection
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {e}")
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
                retry_config=self.config.connection_retry_config,
                retry_exceptions=(
                    aio_pika.exceptions.AMQPConnectionError,
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
                f"Failed to establish connection to RabbitMQ after retries: {e}"
            )
            if self.connection:
                try:
                    await self.connection.close()
                except Exception as close_error:
                    logger.error(
                        f"Error closing connection after connect failure: {close_error}"
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
        channel = await self.get_channel(queue_name)
        if channel is None:
            if self.connection and not self.connection.is_closed:
                # Try to create a new channel
                async with self.create_channel(queue_name) as new_channel:
                    yield new_channel
            else:
                raise RuntimeError(
                    f"Cannot get channel for queue {queue_name}: no connection available"
                )
        else:
            try:
                yield channel
            finally:
                # We don't close the channel here as it's managed by the consumer
                pass


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
        connection_retry_config = RetryConfig()
        consumer_retry_config = RetryConfig(
            max_retries=30, initial_delay=5, max_delay=60.0, backoff_factor=3.0
        )

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

        config = AioPikaWorkerConfig(
            url=broker_url,
            exchange=exchange,
            prefetch_count=prefetch_count,
            connection_retry_config=connection_retry_config,
            consumer_retry_config=consumer_retry_config,
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
            logger.info(
                f"Shutdown in progress. Requeuing scheduled message for {self.queue_name}"
            )
            try:
                # Use channel context for requeuing
                async with self.consumer.get_channel_ctx(self.queue_name):
                    await aio_pika_message.reject(requeue=True)
            except RuntimeError:
                logger.warning(
                    f"Could not requeue scheduled message during shutdown - channel not available"
                )
            except Exception as e:
                logger.error(
                    f"Failed to requeue scheduled message during shutdown: {e}"
                )
            return

        async with self.consumer.lock:
            task = asyncio.create_task(self.handle_message(aio_pika_message))
            self.consumer.tasks.add(task)
            task.add_done_callback(self.handle_message_consume_done)

    def handle_message_consume_done(self, task: asyncio.Task[Any]) -> None:
        self.consumer.tasks.discard(task)
        if task.cancelled():
            logger.warning(f"Scheduled task for {self.queue_name} was cancelled")
            return

        if (error := task.exception()) is not None:
            logger.exception(
                f"Error processing scheduled action {self.queue_name}", exc_info=error
            )

    async def handle_message(
        self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage
    ) -> None:

        if self.consumer.shutdown_event.is_set():
            logger.info(f"Shutdown event set. Requeuing message for {self.queue_name}")
            try:
                # Use channel context for requeuing
                async with self.consumer.get_channel_ctx(self.queue_name):
                    await aio_pika_message.reject(requeue=True)
                return
            except RuntimeError:
                logger.warning(
                    f"Could not requeue message during shutdown - channel not available"
                )
            except Exception as e:
                logger.error(f"Failed to requeue message during shutdown: {e}")
                return

        sig = inspect.signature(self.scheduled_action.callable)
        if len(sig.parameters) == 1:

            task = asyncio.create_task(
                self.run_with_context(
                    self.scheduled_action,
                    (ScheduleDispatchData(int(aio_pika_message.body.decode("utf-8"))),),
                    {},
                )
            )

        elif len(sig.parameters) == 0:
            task = asyncio.create_task(
                self.run_with_context(
                    self.scheduled_action,
                    (),
                    {},
                )
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
                f"Error processing scheduled action {self.queue_name}: {e}"
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
            logger.info(
                f"Shutdown in progress. Requeuing message for {self.queue_name}"
            )
            try:
                # Use channel context for requeuing
                async with self.consumer.get_channel_ctx(self.queue_name):
                    await aio_pika_message.reject(requeue=True)
            except RuntimeError:
                logger.warning(
                    f"Could not requeue message during shutdown - channel not available"
                )
            except Exception as e:
                logger.error(f"Failed to requeue message during shutdown: {e}")
            return

        async with self.consumer.lock:
            task = asyncio.create_task(self.handle_message(aio_pika_message))
            self.consumer.tasks.add(task)
            task.add_done_callback(self.handle_message_consume_done)

    def handle_message_consume_done(self, task: asyncio.Task[Any]) -> None:
        self.consumer.tasks.discard(task)
        if task.cancelled():
            logger.warning(f"Task for queue {self.queue_name} was cancelled")
            return

        if (error := task.exception()) is not None:
            logger.exception(
                f"Error processing message for queue {self.queue_name}", exc_info=error
            )

    async def __call__(
        self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage
    ) -> None:
        await self.message_consumer(aio_pika_message)

    async def handle_reject_message(
        self,
        aio_pika_message: aio_pika.abc.AbstractIncomingMessage,
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

        # If auto_ack is enabled, we cannot retry the message through RabbitMQ reject mechanism
        if self.message_handler.spec.auto_ack:
            if requeue:
                logger.warning(
                    f"Message {message_id} ({self.queue_name}) cannot be requeued because auto_ack is enabled"
                )
            return

        try:
            # Check if we should retry with backoff
            if (
                not requeue
                and self.message_handler.spec.requeue_on_exception
                and exception is not None
            ):
                # Get retry config from consumer
                retry_config = self.consumer.config.consumer_retry_config

                # Check if we reached max retries
                if retry_count >= retry_config.max_retries:
                    logger.warning(
                        f"Message {message_id} ({self.queue_name}) failed after {retry_count} retries, "
                        f"dead-lettering: {str(exception)}"
                    )
                    # Dead-letter the message after max retries
                    async with self.consumer.get_channel_ctx(self.queue_name):
                        await aio_pika_message.reject(requeue=False)
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

                logger.info(
                    f"Message {message_id} ({self.queue_name}) failed with {str(exception)}, "
                    f"retry {retry_count+1}/{retry_config.max_retries} scheduled in {delay:.2f}s"
                )

                # Store retry state for this message
                self.retry_state[message_id] = {
                    "retry_count": retry_count + 1,
                    "last_exception": exception,
                    "next_retry": time.time() + delay,
                }

                # Schedule retry after delay
                asyncio.create_task(
                    self._delayed_retry(
                        aio_pika_message, delay, retry_count + 1, exception
                    )
                )

                # Acknowledge the current message since we'll handle retry ourselves
                async with self.consumer.get_channel_ctx(self.queue_name):
                    await aio_pika_message.ack()
                return

            # Standard reject without retry or with immediate requeue
            async with self.consumer.get_channel_ctx(self.queue_name):
                await aio_pika_message.reject(requeue=requeue)
                if requeue:
                    logger.info(
                        f"Message {message_id} ({self.queue_name}) requeued for immediate retry"
                    )
                else:
                    logger.info(
                        f"Message {message_id} ({self.queue_name}) rejected without requeue"
                    )

        except RuntimeError as e:
            logger.error(
                f"Error rejecting message {message_id} ({self.queue_name}): {e}"
            )
        except Exception as e:
            logger.exception(
                f"Unexpected error rejecting message {message_id} ({self.queue_name}): {e}"
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
            delay: Delay in seconds before retry
            retry_count: The current retry count (after increment)
            exception: The exception that caused the failure
        """
        message_id = aio_pika_message.message_id or str(uuid.uuid4())

        try:
            # Wait for the backoff delay
            await asyncio.sleep(delay)

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

            # Republish the message to the same queue
            async with self.consumer.get_channel_ctx(self.queue_name) as channel:
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

                logger.info(
                    f"Message {message_id} ({self.queue_name}) republished for retry {retry_count}"
                )

        except Exception as e:
            logger.exception(
                f"Failed to execute delayed retry for message {message_id} ({self.queue_name}): {e}"
            )
            # If we fail to republish, try to dead-letter the original message
            try:
                if message_id in self.retry_state:
                    del self.retry_state[message_id]
            except Exception:
                pass

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

        sig = inspect.signature(handler)

        if len(sig.parameters) != 1:
            logger.warning(
                "Handler for topic '%s' must have exactly one parameter which is MessageOf[T extends Message]"
                % routing_key
            )
            return

        parameter = list(sig.parameters.values())[0]

        param_origin = get_origin(parameter.annotation)

        if param_origin is not MessageOf:
            logger.warning(
                "Handler for topic '%s' must have exactly one parameter of type Message"
                % routing_key
            )
            return

        if len(parameter.annotation.__args__) != 1:
            logger.warning(
                "Handler for topic '%s' must have exactly one parameter of type Message"
                % routing_key
            )
            return

        message_type = parameter.annotation.__args__[0]

        if not issubclass(message_type, BaseModel):
            logger.warning(
                "Handler for topic '%s' must have exactly one parameter of type MessageOf[BaseModel]"
                % routing_key
            )
            return

        builded_message = AioPikaMessage(aio_pika_message, message_type)

        incoming_message_spec = MessageHandler.get_message_incoming(handler)
        assert incoming_message_spec is not None

        with provide_shutdown_state(self.consumer.shutdown_state):
            async with self.consumer.uow_context_provider(
                AppTransactionContext(
                    controller_member_reflect=handler_data.controller_member,
                    transaction_data=MessageBusTransactionData(
                        message=builded_message,
                        topic=routing_key,
                    ),
                )
            ):
                ctx: AsyncContextManager[Any]
                if incoming_message_spec.timeout is not None:
                    ctx = asyncio.timeout(incoming_message_spec.timeout)
                else:
                    ctx = none_context()
                async with ctx:
                    try:
                        with provide_bus_message_controller(
                            AioPikaMessageBusController(aio_pika_message)
                        ):
                            await handler(builded_message)
                        if not incoming_message_spec.auto_ack:
                            with suppress(aio_pika.MessageProcessError):
                                # Use channel context for acknowledgement
                                async with self.consumer.get_channel_ctx(
                                    self.queue_name
                                ):
                                    await aio_pika_message.ack()
                    except BaseException as base_exc:
                        # Get message id for logging
                        message_id = aio_pika_message.message_id or str(uuid.uuid4())

                        # Extract retry count from headers if available
                        headers = aio_pika_message.headers or {}
                        retry_count = int(str(headers.get("x-retry-count", 0)))

                        # Process exception handler if configured
                        if incoming_message_spec.exception_handler is not None:
                            try:
                                incoming_message_spec.exception_handler(base_exc)
                            except Exception as nested_exc:
                                logger.exception(
                                    f"Error processing exception handler for message {message_id}: {base_exc} | {nested_exc}"
                                )
                        else:
                            logger.exception(
                                f"Error processing message {message_id} on topic {routing_key}: {str(base_exc)}"
                            )

                        # Handle rejection with retry logic
                        if incoming_message_spec.requeue_on_exception:
                            # Use our retry with backoff mechanism
                            await self.handle_reject_message(
                                aio_pika_message,
                                requeue=False,  # Don't requeue directly, use our backoff mechanism
                                retry_count=retry_count,
                                exception=base_exc,
                            )
                        else:
                            # Message shouldn't be retried, reject it
                            await self.handle_reject_message(
                                aio_pika_message, requeue=False, exception=base_exc
                            )
                    else:
                        # Message processed successfully, log and clean up any retry state
                        message_id = aio_pika_message.message_id or str(uuid.uuid4())
                        if message_id in self.retry_state:
                            del self.retry_state[message_id]

                        # Log success with retry information if applicable
                        headers = aio_pika_message.headers or {}
                        if "x-retry-count" in headers:
                            retry_count = int(str(headers.get("x-retry-count", 0)))
                            logger.info(
                                f"Message {message_id}#{self.queue_name} processed successfully after {retry_count} retries"
                            )
                        else:
                            logger.info(
                                f"Message {message_id}#{self.queue_name} processed successfully"
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
        async with self.lifecycle():
            for instance_class in self.app.controllers:
                controller = MessageBusController.get_messagebus(instance_class)

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

            broker_backend = get_message_broker_backend_from_url(url=self.backend_url)

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
            logger.info("Shutting down - signal received")
            # Schedule the shutdown to run in the event loop
            asyncio.create_task(self._graceful_shutdown())
            # wait until the shutdown is complete

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            loop = runner.get_loop()
            loop.add_signal_handler(signal.SIGINT, on_shutdown, loop)
            # Add graceful shutdown handler for SIGTERM as well
            loop.add_signal_handler(signal.SIGTERM, on_shutdown, loop)
            runner.run(self.start_async())

    async def _graceful_shutdown(self) -> None:
        """Handles graceful shutdown process"""
        logger.info("Initiating graceful shutdown sequence")
        # Use the comprehensive close method that handles shutdown, task waiting and connection cleanup

        self.consumer.shutdown()
        logger.info("Graceful shutdown completed")


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

    async def retry(self) -> None:
        """
        Retry the message immediately by rejecting with requeue flag.
        This doesn't use the exponential backoff mechanism.
        """
        callback = self._get_callback()
        await callback.handle_reject_message(self.aio_pika_message, requeue=True)

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
                )
            )

            # Acknowledge the current message since we'll republish
            await self.aio_pika_message.ack()

        except Exception as e:
            logger.exception(f"Failed to schedule retry_later: {e}")
            # Fall back to immediate retry
            await self.aio_pika_message.reject(requeue=True)
