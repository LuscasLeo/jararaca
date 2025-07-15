import asyncio
import contextlib
import logging
import random
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
from aio_pika import connect_robust
from aio_pika.abc import AbstractChannel, AbstractRobustConnection
from aio_pika.exceptions import (
    AMQPChannelError,
    AMQPConnectionError,
    AMQPError,
    ChannelClosed,
    ConnectionClosed,
)
from aio_pika.pool import Pool

from jararaca.broker_backend import MessageBrokerBackend
from jararaca.broker_backend.mapper import get_message_broker_backend_from_url
from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.lifecycle import AppLifecycle
from jararaca.microservice import Microservice
from jararaca.scheduler.decorators import (
    ScheduledAction,
    ScheduledActionData,
    get_type_scheduled_actions,
)
from jararaca.scheduler.types import DelayedMessageData
from jararaca.utils.rabbitmq_utils import RabbitmqUtils
from jararaca.utils.retry import RetryConfig, retry_with_backoff

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

    def __init__(self, url: str, config: "BeatWorkerConfig | None" = None) -> None:
        self.url = url
        self.config = config or BeatWorkerConfig()
        self.connection_healthy = False
        self.reconnection_in_progress = False
        self.shutdown_event = asyncio.Event()
        self.health_check_task: asyncio.Task[Any] | None = None
        self.reconnection_lock = asyncio.Lock()

        self.conn_pool: "Pool[AbstractRobustConnection]" = Pool(
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

    async def _create_connection(self) -> AbstractRobustConnection:
        """
        Create a robust connection to the RabbitMQ server with retry logic.
        """

        async def _establish_connection() -> AbstractRobustConnection:
            logger.info("Establishing connection to RabbitMQ")
            connection = await connect_robust(
                self.url,
                heartbeat=self.config.connection_heartbeat_interval,
            )
            logger.info("Connected to RabbitMQ successfully")
            return connection

        return await retry_with_backoff(
            _establish_connection,
            retry_config=self.config.connection_retry_config,
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
            retry_config=self.config.connection_retry_config,
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
            logger.info(f"Dispatching message to {action_id} at {timestamp}")
            async with self.channel_pool.acquire() as channel:
                exchange = await RabbitmqUtils.get_main_exchange(channel, self.exchange)

                await exchange.publish(
                    aio_pika.Message(body=str(timestamp).encode()),
                    routing_key=action_id,
                )
                logger.info(f"Dispatched message to {action_id} at {timestamp}")

        try:
            await retry_with_backoff(
                _dispatch,
                retry_config=self.config.dispatch_retry_config,
                retry_exceptions=(
                    AMQPConnectionError,
                    AMQPChannelError,
                    ChannelClosed,
                    ConnectionClosed,
                    AMQPError,
                ),
            )
        except Exception as e:
            logger.error(
                f"Failed to dispatch message to {action_id} after retries: {e}"
            )
            # Trigger reconnection if dispatch fails
            if not self.reconnection_in_progress:
                asyncio.create_task(self._handle_reconnection())
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
                retry_config=self.config.dispatch_retry_config,
                retry_exceptions=(
                    AMQPConnectionError,
                    AMQPChannelError,
                    ChannelClosed,
                    ConnectionClosed,
                    AMQPError,
                ),
            )
        except Exception as e:
            logger.error(f"Failed to dispatch delayed message after retries: {e}")
            # Trigger reconnection if dispatch fails
            if not self.reconnection_in_progress:
                asyncio.create_task(self._handle_reconnection())
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
            logger.info("Initializing RabbitMQ connection...")
            await retry_with_backoff(
                _initialize,
                retry_config=self.config.connection_retry_config,
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
            logger.info("RabbitMQ connection initialized successfully")

            # Start health monitoring
            self.health_check_task = asyncio.create_task(
                self._monitor_connection_health()
            )

        except Exception as e:
            logger.error(f"Failed to initialize RabbitMQ after retries: {e}")
            raise

    async def dispose(self) -> None:
        """Clean up resources"""
        logger.info("Disposing RabbitMQ broker dispatcher")
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
        """Monitor connection health and trigger reconnection if needed"""
        while not self.shutdown_event.is_set():
            try:
                await asyncio.sleep(self.config.health_check_interval)

                if self.shutdown_event.is_set():
                    break

                # Check connection health
                if not await self._is_connection_healthy():
                    logger.warning(
                        "Connection health check failed, triggering reconnection"
                    )
                    if not self.reconnection_in_progress:
                        asyncio.create_task(self._handle_reconnection())

            except asyncio.CancelledError:
                logger.info("Connection health monitoring cancelled")
                break
            except Exception as e:
                logger.error(f"Error in connection health monitoring: {e}")
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
            logger.debug(f"Connection health check failed: {e}")
            return False

    async def _handle_reconnection(self) -> None:
        """Handle reconnection process with exponential backoff"""
        async with self.reconnection_lock:
            if self.reconnection_in_progress:
                return

            self.reconnection_in_progress = True
            self.connection_healthy = False

        logger.info("Starting reconnection process")

        attempt = 0
        while not self.shutdown_event.is_set():
            try:
                attempt += 1
                logger.info(f"Reconnection attempt {attempt}")

                # Close existing pools
                await self._cleanup_pools()

                # Recreate pools
                self.conn_pool = Pool(
                    self._create_connection,
                    max_size=self.config.max_pool_size,
                )
                self.channel_pool = Pool(
                    self._create_channel,
                    max_size=self.config.max_pool_size,
                )

                # Test connection
                if await self._is_connection_healthy():
                    self.connection_healthy = True
                    logger.info("Reconnection successful")
                    break
                else:
                    raise ConnectionError(
                        "Connection health check failed after reconnection"
                    )

            except Exception as e:
                logger.error(f"Reconnection attempt {attempt} failed: {e}")

                if self.shutdown_event.is_set():
                    break

                # Calculate backoff delay
                delay = self.config.reconnection_delay * (2 ** min(attempt - 1, 10))
                if self.config.connection_retry_config.jitter:
                    jitter_amount = delay * 0.25
                    delay = delay + random.uniform(-jitter_amount, jitter_amount)
                    delay = max(delay, 0.1)

                delay = min(delay, self.config.connection_retry_config.max_delay)

                logger.info(f"Retrying reconnection in {delay:.2f} seconds")
                await asyncio.sleep(delay)

        self.reconnection_in_progress = False

    async def _cleanup_pools(self) -> None:
        """Clean up existing connection pools"""
        try:
            if hasattr(self, "channel_pool"):
                await self.channel_pool.close()
        except Exception as e:
            logger.warning(f"Error closing channel pool: {e}")

        try:
            if hasattr(self, "conn_pool"):
                await self.conn_pool.close()
        except Exception as e:
            logger.warning(f"Error closing connection pool: {e}")

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
    url: str, config: "BeatWorkerConfig | None" = None
) -> _MessageBrokerDispatcher:
    """
    Factory function to create a message broker instance from a URL.
    Currently, only RabbitMQ is supported.
    """
    if url.startswith("amqp://") or url.startswith("amqps://"):
        return _RabbitMQBrokerDispatcher(url=url, config=config)
    else:
        raise ValueError(f"Unsupported message broker URL: {url}")


# endregion


@dataclass
class BeatWorkerConfig:
    """Configuration for beat worker connection resilience"""

    connection_retry_config: RetryConfig = field(
        default_factory=lambda: RetryConfig(
            max_retries=10,
            initial_delay=2.0,
            max_delay=60.0,
            backoff_factor=2.0,
            jitter=True,
        )
    )
    dispatch_retry_config: RetryConfig = field(
        default_factory=lambda: RetryConfig(
            max_retries=3,
            initial_delay=1.0,
            max_delay=10.0,
            backoff_factor=2.0,
            jitter=True,
        )
    )
    connection_heartbeat_interval: float = 30.0
    health_check_interval: float = 15.0
    max_reconnection_attempts: int = -1  # Infinite retries
    reconnection_delay: float = 5.0

    # Connection establishment timeouts
    connection_wait_timeout: float = 300.0  # 5 minutes to wait for initial connection
    reconnection_wait_timeout: float = 600.0  # 10 minutes to wait for reconnection

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
    ) -> None:
        self.app = app
        self.config = config or BeatWorkerConfig()

        self.broker: _MessageBrokerDispatcher = _get_message_broker_dispatcher_from_url(
            broker_url, self.config
        )
        self.backend: MessageBrokerBackend = get_message_broker_backend_from_url(
            backend_url
        )

        self.interval = interval
        self.scheduler_names = scheduled_action_names
        self.container = Container(self.app)
        self.uow_provider = UnitOfWorkContextProvider(app, self.container)

        self.shutdown_event = asyncio.Event()

        self.lifecycle = AppLifecycle(app, self.container)

    def run(self) -> None:

        def on_shutdown(loop: asyncio.AbstractEventLoop) -> None:
            logger.info("Shutting down - signal received")
            # Schedule the shutdown to run in the event loop
            asyncio.create_task(self._graceful_shutdown())

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            loop = runner.get_loop()
            loop.add_signal_handler(signal.SIGINT, on_shutdown, loop)
            # Add graceful shutdown handler for SIGTERM as well
            loop.add_signal_handler(signal.SIGTERM, on_shutdown, loop)
            runner.run(self.start_scheduler())

    async def start_scheduler(self) -> None:
        """
        Declares the scheduled actions and starts the scheduler.
        This is the main entry point for the scheduler.
        """
        async with self.lifecycle():

            scheduled_actions = _extract_scheduled_actions(
                self.app, self.container, self.scheduler_names
            )

            # Initialize and wait for connection to be established
            logger.info("Initializing broker connection...")
            await self.broker.initialize(scheduled_actions)

            # Wait for connection to be healthy before starting scheduler
            logger.info("Waiting for connection to be established...")
            await self._wait_for_broker_connection()

            logger.info("Connection established, starting scheduler...")
            await self.run_scheduled_actions(scheduled_actions)

    async def run_scheduled_actions(
        self, scheduled_actions: list[ScheduledActionData]
    ) -> None:

        logger.info("Starting scheduled actions processing loop")

        # Ensure we have a healthy connection before starting the main loop
        if (
            hasattr(self.broker, "connection_healthy")
            and not self.broker.connection_healthy
        ):
            logger.warning(
                "Connection not healthy at start of processing loop, waiting..."
            )
            await self._wait_for_broker_reconnection()

        while not self.shutdown_event.is_set():
            # Check connection health before processing scheduled actions
            if (
                hasattr(self.broker, "connection_healthy")
                and not self.broker.connection_healthy
            ):
                logger.warning(
                    "Broker connection is not healthy, waiting for reconnection..."
                )
                await self._wait_for_broker_reconnection()
                continue

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
                                logger.info(
                                    f"Skipping {func.__module__}.{func.__qualname__} until {next_run}"
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
                            await self.broker.dispatch_scheduled_action(
                                ScheduledAction.get_function_id(func),
                                now,
                            )

                            await self.backend.set_last_dispatch_time(
                                ScheduledAction.get_function_id(func), now
                            )

                            logger.info(
                                f"Scheduled {func.__module__}.{func.__qualname__} at {now}"
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to dispatch scheduled action {func.__module__}.{func.__qualname__}: {e}"
                            )
                            # Continue with other scheduled actions even if one fails
                            continue

                except Exception as e:
                    logger.error(
                        f"Error processing scheduled action {func.__module__}.{func.__qualname__}: {e}"
                    )
                    # Continue with other scheduled actions even if one fails
                    continue

            # Handle delayed messages
            try:
                delayed_messages = await self.backend.dequeue_next_delayed_messages(now)
                for delayed_message_data in delayed_messages:
                    try:
                        await self.broker.dispatch_delayed_message(delayed_message_data)
                    except Exception as e:
                        logger.error(f"Failed to dispatch delayed message: {e}")
                        # Continue with other delayed messages even if one fails
                        continue
            except Exception as e:
                logger.error(f"Error processing delayed messages: {e}")

            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.shutdown_event.wait(), self.interval)

        logger.info("Scheduler stopped")

        try:
            await self.backend.dispose()
        except Exception as e:
            logger.error(f"Error disposing backend: {e}")

        try:
            await self.broker.dispose()
        except Exception as e:
            logger.error(f"Error disposing broker: {e}")

    async def _graceful_shutdown(self) -> None:
        """Handles graceful shutdown process"""
        logger.info("Initiating graceful shutdown sequence")
        self.shutdown_event.set()
        logger.info("Graceful shutdown completed")

    async def _wait_for_broker_connection(self) -> None:
        """
        Wait for the broker connection to be established and healthy.
        This ensures the scheduler doesn't start until RabbitMQ is ready.
        """
        max_wait_time = self.config.connection_wait_timeout
        check_interval = 2.0  # Check every 2 seconds
        elapsed_time = 0.0

        logger.info(
            f"Waiting for broker connection to be established (timeout: {max_wait_time}s)..."
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
                logger.info("Broker connection is healthy")
                return

            # If broker doesn't have health status, try a simple health check
            if not hasattr(self.broker, "connection_healthy"):
                try:
                    # For non-RabbitMQ brokers, assume connection is ready after initialization
                    logger.info("Broker connection assumed to be ready")
                    return
                except Exception as e:
                    logger.debug(f"Broker connection check failed: {e}")

            if elapsed_time % 10.0 == 0.0:  # Log every 10 seconds
                logger.info(
                    f"Still waiting for broker connection... ({elapsed_time:.1f}s elapsed)"
                )

            await asyncio.sleep(check_interval)
            elapsed_time += check_interval

        raise ConnectionError(
            f"Broker connection not established after {max_wait_time} seconds"
        )

    async def _wait_for_broker_reconnection(self) -> None:
        """
        Wait for the broker to reconnect when connection is lost during operation.
        This pauses the scheduler until the connection is restored.
        """
        max_wait_time = self.config.reconnection_wait_timeout
        check_interval = 5.0  # Check every 5 seconds
        elapsed_time = 0.0

        logger.info(f"Waiting for broker reconnection (timeout: {max_wait_time}s)...")

        while elapsed_time < max_wait_time:
            if self.shutdown_event.is_set():
                logger.info("Shutdown requested while waiting for broker reconnection")
                return

            # Check if broker connection is healthy again
            if (
                hasattr(self.broker, "connection_healthy")
                and self.broker.connection_healthy
            ):
                logger.info("Broker connection restored, resuming scheduler")
                return

            if elapsed_time % 30.0 == 0.0:  # Log every 30 seconds
                logger.info(
                    f"Still waiting for broker reconnection... ({elapsed_time:.1f}s elapsed)"
                )

            await asyncio.sleep(check_interval)
            elapsed_time += check_interval

        logger.error(f"Broker connection not restored after {max_wait_time} seconds")
        # Don't raise an exception here, just continue and let the scheduler retry
        # This allows the scheduler to be more resilient to long-term connection issues
