import asyncio
import inspect
import logging
import signal
from abc import ABC
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, AsyncContextManager, AsyncGenerator, Type, get_origin
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
)
from jararaca.scheduler.decorators import ScheduledActionData
from jararaca.utils.rabbitmq_utils import RabbitmqUtils

logger = logging.getLogger(__name__)


@dataclass
class AioPikaWorkerConfig:
    url: str
    exchange: str
    prefetch_count: int


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
        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.connection: aio_pika.abc.AbstractConnection | None = None
        self.channels: dict[str, aio_pika.abc.AbstractChannel] = {}

    async def consume(self) -> None:

        self.connection = await aio_pika.connect(self.config.url)

        # Create a main channel just for checking infrastructure
        main_channel = await self.connection.channel()

        # Get existing exchange and queues to verify infrastructure is in place
        try:
            await RabbitmqUtils.get_main_exchange(
                channel=main_channel,
                exchange_name=self.config.exchange,
            )

            await RabbitmqUtils.get_dl_exchange(channel=main_channel)
            await RabbitmqUtils.get_dl_queue(channel=main_channel)
        except (ChannelNotFoundEntity, ChannelClosed, AMQPError) as e:
            logger.critical(
                f"Required exchange or queue infrastructure not found."
                f"Please use the declare command first to create the required infrastructure. Error: {e}"
            )
            self.shutdown_event.set()
            await main_channel.close()
            await self.connection.close()
            return

        # Close the main channel as we'll create separate channels for each queue
        await main_channel.close()

        # Setup message handlers
        for handler in self.message_handler_set:
            queue_name = f"{handler.message_type.MESSAGE_TOPIC}.{handler.instance_callable.__module__}.{handler.instance_callable.__qualname__}"
            routing_key = f"{handler.message_type.MESSAGE_TOPIC}.#"

            self.incoming_map[queue_name] = handler

            try:
                # Create a dedicated channel for this queue
                channel = await self.connection.channel()
                await channel.set_qos(prefetch_count=self.config.prefetch_count)
                self.channels[queue_name] = channel

                queue = await RabbitmqUtils.get_queue(
                    channel=channel, queue_name=queue_name
                )
            except (ChannelNotFoundEntity, ChannelClosed, AMQPError) as e:
                logger.error(
                    f"Queue '{queue_name}' not found or channel error. "
                    f"Please use the declare command first to create the queue. Error: {e}"
                )
                continue

            await queue.consume(
                callback=MessageHandlerCallback(
                    consumer=self,
                    queue_name=queue_name,
                    routing_key=routing_key,
                    message_handler=handler,
                ),
                no_ack=handler.spec.auto_ack,
            )

            logger.info(f"Consuming message handler {queue_name} on dedicated channel")

        # Setup scheduled actions
        for scheduled_action in self.scheduled_actions:
            queue_name = f"{scheduled_action.callable.__module__}.{scheduled_action.callable.__qualname__}"
            routing_key = queue_name

            try:
                # Create a dedicated channel for this queue
                channel = await self.connection.channel()
                await channel.set_qos(prefetch_count=self.config.prefetch_count)
                self.channels[queue_name] = channel

                queue = await RabbitmqUtils.get_queue(
                    channel=channel, queue_name=queue_name
                )
            except (ChannelNotFoundEntity, ChannelClosed, AMQPError) as e:
                logger.error(
                    f"Scheduler queue '{queue_name}' not found or channel error. "
                    f"Please use the declare command first to create the queue. Error: {e}"
                )
                continue

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

        # Wait for shutdown signal
        await self.shutdown_event.wait()
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

        config = AioPikaWorkerConfig(
            url=broker_url,
            exchange=exchange,
            prefetch_count=prefetch_count,
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
                await aio_pika_message.reject(requeue=True)
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
                await aio_pika_message.reject(requeue=True)
                return
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

    async def message_consumer(
        self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage
    ) -> None:
        if self.consumer.shutdown_event.is_set():
            logger.info(
                f"Shutdown in progress. Requeuing message for {self.queue_name}"
            )
            try:
                await aio_pika_message.reject(requeue=True)
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
    ) -> None:
        if self.message_handler.spec.auto_ack is False:
            await aio_pika_message.reject(requeue=requeue)
        elif requeue:
            logger.warning(
                f"Message {aio_pika_message.message_id} ({self.queue_name}) cannot be requeued because auto_ack is enabled"
            )

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
                            await aio_pika_message.ack()
                except BaseException as base_exc:
                    if incoming_message_spec.exception_handler is not None:
                        try:
                            incoming_message_spec.exception_handler(base_exc)
                        except Exception as nested_exc:
                            logger.exception(
                                f"Error processing exception handler: {base_exc} | {nested_exc}"
                            )
                    else:
                        logger.exception(
                            f"Error processing message on topic {routing_key}"
                        )
                    if incoming_message_spec.requeue_on_exception:
                        await self.handle_reject_message(aio_pika_message, requeue=True)
                    else:
                        await self.handle_reject_message(
                            aio_pika_message, requeue=False
                        )
                else:
                    logger.info(
                        f"Message {aio_pika_message.message_id}#{self.queue_name} processed successfully"
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
        await self.consumer.close()
        logger.info("Graceful shutdown completed")


class AioPikaMessageBusController(BusMessageController):
    def __init__(self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage):
        self.aio_pika_message = aio_pika_message

    async def ack(self) -> None:
        await self.aio_pika_message.ack()

    async def nack(self) -> None:
        await self.aio_pika_message.nack()

    async def reject(self) -> None:
        await self.aio_pika_message.reject()

    async def retry(self) -> None:
        await self.aio_pika_message.reject(requeue=True)

    async def retry_later(self, delay: int) -> None:
        raise NotImplementedError("Not implemented")
