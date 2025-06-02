import asyncio
import contextlib
import logging
import signal
import time
from abc import ABC, abstractmethod
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

    def __init__(self, url: str) -> None:
        self.url = url

        self.conn_pool: "Pool[AbstractRobustConnection]" = Pool(
            self._create_connection,
            max_size=10,
        )

        self.channel_pool: "Pool[AbstractChannel]" = Pool(
            self._create_channel,
            max_size=10,
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
        Create a connection to the RabbitMQ server.
        This is used to send messages to the RabbitMQ server.
        """
        connection = await connect_robust(self.url)
        return connection

    async def _create_channel(self) -> AbstractChannel:
        """
        Create a channel to the RabbitMQ server.
        This is used to send messages to the RabbitMQ server.
        """
        async with self.conn_pool.acquire() as connection:
            channel = await connection.channel()
            return channel

    async def dispatch_scheduled_action(self, action_id: str, timestamp: int) -> None:
        """
        Dispatch a message to the RabbitMQ server.
        This is used to send a message to the RabbitMQ server
        to trigger the scheduled action.
        """

        logger.info(f"Dispatching message to {action_id} at {timestamp}")
        async with self.channel_pool.acquire() as channel:
            exchange = await RabbitmqUtils.get_main_exchange(channel, self.exchange)

            await exchange.publish(
                aio_pika.Message(body=str(timestamp).encode()),
                routing_key=action_id,
            )
            logger.info(f"Dispatched message to {action_id} at {timestamp}")

    async def dispatch_delayed_message(
        self, delayed_message: DelayedMessageData
    ) -> None:
        """
        Dispatch a delayed message to the RabbitMQ server.
        This is used to send a message to the RabbitMQ server
        to trigger the scheduled action.
        """
        async with self.channel_pool.acquire() as channel:

            exchange = await RabbitmqUtils.get_main_exchange(channel, self.exchange)
            await exchange.publish(
                aio_pika.Message(
                    body=delayed_message.payload,
                ),
                routing_key=f"{delayed_message.message_topic}.",
            )

    async def initialize(self, scheduled_actions: list[ScheduledActionData]) -> None:
        """
        Initialize the RabbitMQ server.
        This is used to create the exchange and queues for the scheduled actions.
        """

        async with self.channel_pool.acquire() as channel:
            await RabbitmqUtils.get_main_exchange(channel, self.exchange)

            for sched_act_data in scheduled_actions:
                queue_name = ScheduledAction.get_function_id(sched_act_data.callable)

                # Try to get existing queue
                await RabbitmqUtils.get_scheduled_action_queue(
                    channel=channel,
                    queue_name=queue_name,
                )

    async def dispose(self) -> None:
        await self.channel_pool.close()
        await self.conn_pool.close()


def _get_message_broker_dispatcher_from_url(url: str) -> _MessageBrokerDispatcher:
    """
    Factory function to create a message broker instance from a URL.
    Currently, only RabbitMQ is supported.
    """
    if url.startswith("amqp://") or url.startswith("amqps://"):
        return _RabbitMQBrokerDispatcher(url=url)
    else:
        raise ValueError(f"Unsupported message broker URL: {url}")


# endregion


class BeatWorker:

    def __init__(
        self,
        app: Microservice,
        interval: int,
        broker_url: str,
        backend_url: str,
        scheduled_action_names: set[str] | None = None,
    ) -> None:
        self.app = app

        self.broker: _MessageBrokerDispatcher = _get_message_broker_dispatcher_from_url(
            broker_url
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

            await self.broker.initialize(scheduled_actions)

            await self.run_scheduled_actions(scheduled_actions)

    async def run_scheduled_actions(
        self, scheduled_actions: list[ScheduledActionData]
    ) -> None:

        while not self.shutdown_event.is_set():
            now = int(time.time())
            for sched_act_data in scheduled_actions:
                func = sched_act_data.callable
                scheduled_action = sched_act_data.spec
                if self.shutdown_event.is_set():
                    break

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
                        next_run: datetime = cron.get_next(datetime).replace(tzinfo=UTC)
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

            for (
                delayed_message_data
            ) in await self.backend.dequeue_next_delayed_messages(now):
                await self.broker.dispatch_delayed_message(delayed_message_data)

            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.shutdown_event.wait(), self.interval)

            # await self.shutdown_event.wait(self.interval)

        logger.info("Scheduler stopped")

        await self.backend.dispose()
        await self.broker.dispose()

    async def _graceful_shutdown(self) -> None:
        """Handles graceful shutdown process"""
        logger.info("Initiating graceful shutdown sequence")
        self.shutdown_event.set()
        logger.info("Graceful shutdown completed")
