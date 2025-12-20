# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import contextlib
import logging
import signal
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

import aio_pika
import croniter
import urllib3
import urllib3.util
import uvloop
from aio_pika import connect
from aio_pika.abc import AbstractChannel, AbstractConnection
from aio_pika.exceptions import (
    AMQPChannelError,
    AMQPConnectionError,
    AMQPError,
    ChannelClosed,
    ConnectionClosed,
)
from aio_pika.pool import Pool
from aiormq.exceptions import ChannelInvalidStateError

from jararaca.broker_backend import MessageBrokerBackend
from jararaca.broker_backend.mapper import get_message_broker_backend_from_url
from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.lifecycle import AppLifecycle
from jararaca.microservice import Microservice, providing_app_type
from jararaca.scheduler.decorators import (
    ScheduledAction,
    ScheduledActionData,
    get_type_scheduled_actions,
)
from jararaca.scheduler.types import DelayedMessageData
from jararaca.utils.rabbitmq_utils import RabbitmqUtils
from jararaca.utils.retry import RetryPolicy, retry_with_backoff

logger = logging.getLogger(__name__)


def _extract_scheduled_actions(
    app: Microservice, container: Container, scheduler_names: set[str] | None = None
) -> list[ScheduledActionData]:
    scheduled_actions: list[ScheduledActionData] = []
    for controllers in app.controllers:

        controller_instance: Any = container.get_by_type(controllers)

        controller_scheduled_actions = get_type_scheduled_actions(controller_instance)

        # Filter scheduled actions by name if scheduler_names is provided
        if scheduler_names is not None:
            filtered_actions = []
            for action in controller_scheduled_actions:
                # Include actions that have a name and it's in the provided set
                if action.spec.name and action.spec.name in scheduler_names:
                    filtered_actions.append(action)
                # Skip actions without names when filtering is active
            controller_scheduled_actions = filtered_actions

        scheduled_actions.extend(controller_scheduled_actions)

    return scheduled_actions


# region Message Broker Dispatcher


class _MessageBrokerDispatcher(ABC):

    @abstractmethod
    async def dispatch_scheduled_action(
        self,
        action_id: str,
        timestamp: int,
    ) -> None:
        """
        Dispatch a message to the message broker.
        This is used to send a message to the message broker
        to trigger the scheduled action.
        """
        raise NotImplementedError("dispatch() is not implemented yet.")

    @abstractmethod
    async def dispatch_delayed_message(
        self,
        delayed_message: DelayedMessageData,
    ) -> None:
        """
        Dispatch a delayed message to the message broker.
        This is used to send a message to the message broker
        to trigger the scheduled action.
        """

        raise NotImplementedError("dispatch_delayed_message() is not implemented yet.")

    @abstractmethod
    async def initialize(self, scheduled_actions: list[ScheduledActionData]) -> None:
        raise NotImplementedError("initialize() is not implemented yet.")

    async def dispose(self) -> None:
        pass


class _RabbitMQBrokerDispatcher(_MessageBrokerDispatcher):

    def __init__(
        self,
        url: str,
        config: "BeatWorkerConfig | None" = None,
        shutdown_event: asyncio.Event | None = None,
    ) -> None:
        self.url = url
        self.config = config or BeatWorkerConfig()
        self.connection_healthy = False
        self.shutdown_event = shutdown_event or asyncio.Event()
        self.health_check_task: asyncio.Task[Any] | None = None

        self.conn_pool: "Pool[AbstractConnection]" = Pool(
            self._create_connection,
            max_size=self.config.max_pool_size,
        )

        self.channel_pool: "Pool[AbstractChannel]" = Pool(
            self._create_channel,
            max_size=self.config.max_pool_size,
        )

        splitted = urllib3.util.parse_url(url)

        assert splitted.scheme in ["amqp", "amqps"], "Invalid URL scheme"

        assert splitted.host, "Invalid URL host"

        assert splitted.query, "Invalid URL query"

        query_params: dict[str, list[str]] = parse_qs(splitted.query)

        assert "exchange" in query_params, "Missing exchange parameter"

        assert query_params["exchange"], "Empty exchange parameter"

        self.exchange = str(query_params["exchange"][0])

    async def _create_connection(self) -> AbstractConnection:
        """
        Create a connection to the RabbitMQ server with retry logic.
        """

        async def _establish_connection() -> AbstractConnection:
            logger.debug("Establishing connection to RabbitMQ")
            connection = await connect(
                self.url,
                heartbeat=self.config.connection_heartbeat_interval,
            )
            logger.debug("Connected to RabbitMQ successfully")
            return connection

        return await retry_with_backoff(
            _establish_connection,
            retry_policy=self.config.connection_retry_config,
            retry_exceptions=(
                AMQPConnectionError,
                ConnectionError,
                OSError,
                TimeoutError,
            ),
        )

    async def _create_channel(self) -> AbstractChannel:
        """
        Create a channel to the RabbitMQ server with retry logic.
        """

        async def _establish_channel() -> AbstractChannel:
            async with self.conn_pool.acquire() as connection:
                channel = await connection.channel()
                return channel

        return await retry_with_backoff(
            _establish_channel,
            retry_policy=self.config.connection_retry_config,
            retry_exceptions=(
                AMQPConnectionError,
                AMQPChannelError,
                ChannelClosed,
                ConnectionError,
            ),
        )

    async def dispatch_scheduled_action(self, action_id: str, timestamp: int) -> None:
        """
        Dispatch a message to the RabbitMQ server with retry logic.
        """
        if not self.connection_healthy:
            await self._wait_for_connection()

        async def _dispatch() -> None:
            logger.debug("Dispatching message to %s at %s", action_id, timestamp)
            async with self.channel_pool.acquire() as channel:
                exchange = await RabbitmqUtils.get_main_exchange(channel, self.exchange)

                await exchange.publish(
                    aio_pika.Message(body=str(timestamp).encode()),
                    routing_key=action_id,
                )
                logger.debug("Dispatched message to %s at %s", action_id, timestamp)

        try:
            await retry_with_backoff(
                _dispatch,
                retry_policy=self.config.dispatch_retry_config,
                retry_exceptions=(
                    AMQPConnectionError,
                    AMQPChannelError,
                    ChannelClosed,
                    ConnectionClosed,
                    AMQPError,
                ),
            )

        except ChannelInvalidStateError as e:
            logger.error(
                "Channel invalid state error when dispatching to %s: %s", action_id, e
            )
            # Trigger shutdown if dispatch fails
            self.shutdown_event.set()
            raise

        except Exception as e:
            logger.error(
                "Failed to dispatch message to %s after retries: %s", action_id, e
            )
            # Trigger shutdown if dispatch fails
            self.shutdown_event.set()
            raise

    async def dispatch_delayed_message(
        self, delayed_message: DelayedMessageData
    ) -> None:
        """
        Dispatch a delayed message to the RabbitMQ server with retry logic.
        """
        if not self.connection_healthy:
            await self._wait_for_connection()

        async def _dispatch() -> None:
            async with self.channel_pool.acquire() as channel:
                exchange = await RabbitmqUtils.get_main_exchange(channel, self.exchange)
                await exchange.publish(
                    aio_pika.Message(
                        body=delayed_message.payload,
                    ),
                    routing_key=f"{delayed_message.message_topic}.",
                )

        try:
            await retry_with_backoff(
                _dispatch,
                retry_policy=self.config.dispatch_retry_config,
                retry_exceptions=(
                    AMQPConnectionError,
                    AMQPChannelError,
                    ChannelClosed,
                    ConnectionClosed,
                    AMQPError,
                ),
            )
        except Exception as e:
            logger.error("Failed to dispatch delayed message after retries: %s", e)
            # Trigger shutdown if dispatch fails
            self.shutdown_event.set()
            raise

    async def initialize(self, scheduled_actions: list[ScheduledActionData]) -> None:
        """
        Initialize the RabbitMQ server with retry logic.
        """

        async def _initialize() -> None:
            async with self.channel_pool.acquire() as channel:
                await RabbitmqUtils.get_main_exchange(channel, self.exchange)

                for sched_act_data in scheduled_actions:
                    queue_name = ScheduledAction.get_function_id(
                        sched_act_data.callable
                    )

                    # Try to get existing queue
                    await RabbitmqUtils.get_scheduled_action_queue(
                        channel=channel,
                        queue_name=queue_name,
                    )

        try:
            logger.debug("Initializing RabbitMQ connection...")
            await retry_with_backoff(
                _initialize,
                retry_policy=self.config.connection_retry_config,
                retry_exceptions=(
                    AMQPConnectionError,
                    AMQPChannelError,
                    ChannelClosed,
                    ConnectionClosed,
                    AMQPError,
                ),
            )

            # Verify connection is actually healthy after initialization
            if not await self._is_connection_healthy():
                logger.warning(
                    "Connection health check failed after initialization, retrying..."
                )
                # Wait a bit and try again
                await asyncio.sleep(2.0)
                if not await self._is_connection_healthy():
                    raise ConnectionError("Connection not healthy after initialization")

            self.connection_healthy = True
            logger.debug("RabbitMQ connection initialized successfully")

            # Start health monitoring
            self.health_check_task = asyncio.create_task(
                self._monitor_connection_health()
            )

        except Exception as e:
            logger.error("Failed to initialize RabbitMQ after retries: %s", e)
            raise

    async def dispose(self) -> None:
        """Clean up resources"""
        logger.debug("Disposing RabbitMQ broker dispatcher")
        self.shutdown_event.set()

        # Cancel health monitoring
        if self.health_check_task:
            self.health_check_task.cancel()
            try:
                await self.health_check_task
            except asyncio.CancelledError:
                pass

        # Clean up pools
        await self._cleanup_pools()

    async def _monitor_connection_health(self) -> None:
        """Monitor connection health and trigger shutdown if needed"""
        while not self.shutdown_event.is_set():
            try:
                await asyncio.sleep(self.config.health_check_interval)

                if self.shutdown_event.is_set():
                    break

                # Check connection health
                if not await self._is_connection_healthy():
                    logger.error("Connection health check failed, triggering shutdown")
                    self.shutdown_event.set()
                    break

            except asyncio.CancelledError:
                logger.debug("Connection health monitoring cancelled")
                break
            except Exception as e:
                logger.error("Error in connection health monitoring: %s", e)
                await asyncio.sleep(5)  # Wait before retrying

    async def _is_connection_healthy(self) -> bool:
        """Check if the connection is healthy"""
        try:
            # Try to acquire a connection from the pool
            async with self.conn_pool.acquire() as connection:
                if connection.is_closed:
                    return False

                # Try to create a channel to test connection
                channel = await connection.channel()
                await channel.close()
                return True

        except Exception as e:
            logger.debug("Connection health check failed: %s", e)
            return False

    async def _cleanup_pools(self) -> None:
        """Clean up existing connection pools"""
        try:
            if hasattr(self, "channel_pool"):
                await self.channel_pool.close()
        except Exception as e:
            logger.warning("Error closing channel pool: %s", e)

        try:
            if hasattr(self, "conn_pool"):
                await self.conn_pool.close()
        except Exception as e:
            logger.warning("Error closing connection pool: %s", e)

    async def _wait_for_connection(self) -> None:
        """Wait for connection to be healthy"""
        max_wait = 30.0  # Maximum wait time
        wait_time = 0.0

        while not self.connection_healthy and wait_time < max_wait:
            if self.shutdown_event.is_set():
                raise ConnectionError("Shutdown requested while waiting for connection")

            await asyncio.sleep(0.5)
            wait_time += 0.5

        if not self.connection_healthy:
            raise ConnectionError("Connection not healthy after maximum wait time")


def _get_message_broker_dispatcher_from_url(
    url: str,
    config: "BeatWorkerConfig | None" = None,
    shutdown_event: asyncio.Event | None = None,
) -> _MessageBrokerDispatcher:
    """
    Factory function to create a message broker instance from a URL.
    Currently, only RabbitMQ is supported.
    """
    if url.startswith("amqp://") or url.startswith("amqps://"):
        return _RabbitMQBrokerDispatcher(
            url=url, config=config, shutdown_event=shutdown_event
        )
    else:
        raise ValueError(f"Unsupported message broker URL: {url}")


# endregion


@dataclass
class BeatWorkerConfig:
    """Configuration for beat worker connection resilience"""

    connection_retry_config: RetryPolicy = field(
        default_factory=lambda: RetryPolicy(
            max_retries=10,
            initial_delay=2.0,
            max_delay=60.0,
            backoff_factor=2.0,
            jitter=True,
        )
    )
    dispatch_retry_config: RetryPolicy = field(
        default_factory=lambda: RetryPolicy(
            max_retries=3,
            initial_delay=1.0,
            max_delay=10.0,
            backoff_factor=2.0,
            jitter=True,
        )
    )
    connection_heartbeat_interval: float = 30.0
    health_check_interval: float = 15.0

    # Connection establishment timeouts
    connection_wait_timeout: float = 300.0  # 5 minutes to wait for initial connection

    # Pool configuration
    max_pool_size: int = 10
    pool_recycle_time: float = 3600.0  # 1 hour


class BeatWorker:

    def __init__(
        self,
        app: Microservice,
        interval: int,
        broker_url: str,
        backend_url: str,
        scheduled_action_names: set[str] | None = None,
        config: "BeatWorkerConfig | None" = None,
        shutdown_event: asyncio.Event | None = None,
    ) -> None:
        self.shutdown_event = shutdown_event or asyncio.Event()

        self.app = app
        self.config = config or BeatWorkerConfig()

        self.broker: _MessageBrokerDispatcher = _get_message_broker_dispatcher_from_url(
            broker_url, self.config, shutdown_event=self.shutdown_event
        )
        self.backend: MessageBrokerBackend = get_message_broker_backend_from_url(
            backend_url
        )

        self.interval = interval
        self.scheduler_names = scheduled_action_names
        self.container = Container(self.app)
        self.uow_provider = UnitOfWorkContextProvider(app, self.container)

        self.lifecycle = AppLifecycle(app, self.container)

    def run(self) -> None:

        def on_shutdown(loop: asyncio.AbstractEventLoop) -> None:
            logger.debug("Shutting down - signal received")
            # Schedule the shutdown to run in the event loop
            asyncio.create_task(self._graceful_shutdown())

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            loop = runner.get_loop()
            loop.add_signal_handler(signal.SIGINT, on_shutdown, loop)
            # Add graceful shutdown handler for SIGTERM as well
            loop.add_signal_handler(signal.SIGTERM, on_shutdown, loop)
            try:
                runner.run(self.start_scheduler())
            except Exception as e:
                logger.critical(
                    "Scheduler failed to start due to connection error: %s", e
                )
                # Exit with error code 1 to indicate startup failure
                import sys

                sys.exit(1)

    async def start_scheduler(self) -> None:
        """
        Declares the scheduled actions and starts the scheduler.
        This is the main entry point for the scheduler.
        """
        with providing_app_type("beat"):
            async with self.lifecycle():

                scheduled_actions = _extract_scheduled_actions(
                    self.app, self.container, self.scheduler_names
                )

                # Initialize and wait for connection to be established
                logger.debug("Initializing broker connection...")
                await self.broker.initialize(scheduled_actions)

                # Wait for connection to be healthy before starting scheduler
                logger.debug("Waiting for connection to be established...")
                await self._wait_for_broker_connection()

                logger.debug("Connection established, starting scheduler...")
                await self.run_scheduled_actions(scheduled_actions)

    async def run_scheduled_actions(
        self, scheduled_actions: list[ScheduledActionData]
    ) -> None:

        logger.debug("Starting scheduled actions processing loop")

        # Ensure we have a healthy connection before starting the main loop
        if (
            hasattr(self.broker, "connection_healthy")
            and not self.broker.connection_healthy
        ):
            logger.error("Connection not healthy at start of processing loop. Exiting.")
            return

        while not self.shutdown_event.is_set():
            # Check connection health before processing scheduled actions
            if (
                hasattr(self.broker, "connection_healthy")
                and not self.broker.connection_healthy
            ):
                logger.error("Broker connection is not healthy. Exiting.")
                break

            now = int(time.time())
            for sched_act_data in scheduled_actions:
                func = sched_act_data.callable
                scheduled_action = sched_act_data.spec
                if self.shutdown_event.is_set():
                    break

                try:
                    async with self.backend.lock():

                        last_dispatch_time: int | None = (
                            await self.backend.get_last_dispatch_time(
                                ScheduledAction.get_function_id(func)
                            )
                        )

                        if last_dispatch_time is not None:
                            cron = croniter.croniter(
                                scheduled_action.cron, last_dispatch_time
                            )
                            next_run: datetime = cron.get_next(datetime).replace(
                                tzinfo=UTC
                            )
                            if next_run > datetime.now(UTC):
                                logger.debug(
                                    "Skipping %s.%s until %s",
                                    func.__module__,
                                    func.__qualname__,
                                    next_run,
                                )
                                continue

                        if not scheduled_action.allow_overlap:
                            if (
                                await self.backend.get_in_execution_count(
                                    ScheduledAction.get_function_id(func)
                                )
                                > 0
                            ):
                                continue

                        try:
                            start_time = time.perf_counter()
                            await self.broker.dispatch_scheduled_action(
                                ScheduledAction.get_function_id(func),
                                now,
                            )
                            elapsed_time = time.perf_counter() - start_time

                            await self.backend.set_last_dispatch_time(
                                ScheduledAction.get_function_id(func), now
                            )

                            logger.debug(
                                "Scheduled %s.%s at %s in %.4fs",
                                func.__module__,
                                func.__qualname__,
                                now,
                                elapsed_time,
                            )
                        except ChannelInvalidStateError as e:
                            logger.error(
                                "Channel invalid state error when dispatching %s.%s: %s",
                                func.__module__,
                                func.__qualname__,
                                e,
                            )
                            # Trigger shutdown if dispatch fails
                            self.shutdown_event.set()
                            raise
                        except Exception as e:
                            logger.error(
                                "Failed to dispatch scheduled action %s.%s: %s",
                                func.__module__,
                                func.__qualname__,
                                e,
                            )
                            # Continue with other scheduled actions even if one fails
                            continue

                except Exception as e:
                    logger.error(
                        "Error processing scheduled action %s.%s: %s",
                        func.__module__,
                        func.__qualname__,
                        e,
                    )
                    # Continue with other scheduled actions even if one fails
                    continue

            # Handle delayed messages
            try:
                delayed_messages = await self.backend.dequeue_next_delayed_messages(now)
                for delayed_message_data in delayed_messages:
                    try:
                        start_time = time.perf_counter()
                        await self.broker.dispatch_delayed_message(delayed_message_data)
                        elapsed_time = time.perf_counter() - start_time
                        logger.debug(
                            "Dispatched delayed message for topic %s in %.4fs",
                            delayed_message_data.message_topic,
                            elapsed_time,
                        )
                    except Exception as e:
                        logger.error("Failed to dispatch delayed message: %s", e)
                        # Continue with other delayed messages even if one fails
                        continue
            except Exception as e:
                logger.error("Error processing delayed messages: %s", e)

            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.shutdown_event.wait(), self.interval)

        logger.debug("Scheduler stopped")

        try:
            await self.backend.dispose()
        except Exception as e:
            logger.error("Error disposing backend: %s", e)

        try:
            await self.broker.dispose()
        except Exception as e:
            logger.error("Error disposing broker: %s", e)

    async def _graceful_shutdown(self) -> None:
        """Handles graceful shutdown process"""
        logger.debug("Initiating graceful shutdown sequence")
        self.shutdown_event.set()
        logger.debug("Graceful shutdown completed")

    async def _wait_for_broker_connection(self) -> None:
        """
        Wait for the broker connection to be established and healthy.
        This ensures the scheduler doesn't start until RabbitMQ is ready.
        """
        max_wait_time = self.config.connection_wait_timeout
        check_interval = 2.0  # Check every 2 seconds
        elapsed_time = 0.0

        logger.debug(
            "Waiting for broker connection to be established (timeout: %ss)...",
            max_wait_time,
        )

        while elapsed_time < max_wait_time:
            if self.shutdown_event.is_set():
                raise ConnectionError(
                    "Shutdown requested while waiting for broker connection"
                )

            # Check if broker connection is healthy
            if (
                hasattr(self.broker, "connection_healthy")
                and self.broker.connection_healthy
            ):
                logger.debug("Broker connection is healthy")
                return

            # If broker doesn't have health status, try a simple health check
            if not hasattr(self.broker, "connection_healthy"):
                try:
                    # For non-RabbitMQ brokers, assume connection is ready after initialization
                    logger.debug("Broker connection assumed to be ready")
                    return
                except Exception as e:
                    logger.debug("Broker connection check failed: %s", e)

            if elapsed_time % 10.0 == 0.0:  # Log every 10 seconds
                logger.warning(
                    "Still waiting for broker connection... (%.1fs elapsed)",
                    elapsed_time,
                )

            await asyncio.sleep(check_interval)
            elapsed_time += check_interval

        raise ConnectionError(
            f"Broker connection not established after {max_wait_time} seconds"
        )
