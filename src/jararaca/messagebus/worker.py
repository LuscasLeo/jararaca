import asyncio
import inspect
import logging
import signal
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, AsyncContextManager, AsyncGenerator, Type, get_origin

import aio_pika
import aio_pika.abc
import uvloop
from pydantic import BaseModel

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.lifecycle import AppLifecycle
from jararaca.messagebus.bus_message_controller import (
    BusMessageController,
    provide_bus_message_controller,
)
from jararaca.messagebus.decorators import (
    MESSAGE_HANDLER_DATA_SET,
    MessageBusController,
    MessageHandler,
    MessageHandlerData,
)
from jararaca.messagebus.message import Message, MessageOf
from jararaca.microservice import (
    AppTransactionContext,
    MessageBusTransactionData,
    Microservice,
)
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


class AioPikaMicroserviceConsumer:
    def __init__(
        self,
        config: AioPikaWorkerConfig,
        message_handler_set: MESSAGE_HANDLER_DATA_SET,
        uow_context_provider: UnitOfWorkContextProvider,
    ):
        self.config = config
        self.message_handler_set = message_handler_set
        self.incoming_map: dict[str, MessageHandlerData] = {}
        self.uow_context_provider = uow_context_provider
        self.shutdown_event = asyncio.Event()
        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task[Any]] = set()

    async def consume(self) -> None:
        connection = await aio_pika.connect(self.config.url)
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=self.config.prefetch_count)

        # Get existing exchange and DL kit
        try:
            main_ex = await RabbitmqUtils.get_main_exchange(
                channel=channel,
                exchange_name=self.config.exchange,
            )
            dlx, dlq = await RabbitmqUtils.get_dl_kit(channel=channel)
        except Exception as e:
            logger.error(
                f"Required exchange or queue infrastructure not found. "
                f"Please use the declare command first to create the required infrastructure. Error: {e}"
            )
            self.shutdown_event.set()
            return

        for handler in self.message_handler_set:
            queue_name = f"{handler.message_type.MESSAGE_TOPIC}.{handler.instance_callable.__module__}.{handler.instance_callable.__qualname__}"
            routing_key = f"{handler.message_type.MESSAGE_TOPIC}.#"

            self.incoming_map[queue_name] = handler

            # Get existing queue
            try:
                queue = await RabbitmqUtils.get_worker_v1_queue(
                    channel=channel,
                    queue_name=queue_name,
                )
            except Exception as e:
                logger.error(
                    f"Worker queue '{queue_name}' not found. "
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

            logger.info(f"Consuming {queue_name}")

        await self.shutdown_event.wait()
        logger.info("Worker shutting down")

        await self.wait_all_tasks_done()

        await channel.close()
        await connection.close()

    async def wait_all_tasks_done(self) -> None:
        async with self.lock:
            await asyncio.gather(*self.tasks)


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
            return

        async with self.consumer.lock:
            task = asyncio.create_task(self.handle_message(aio_pika_message))
            self.consumer.tasks.add(task)
            task.add_done_callback(self.handle_message_consume_done)

    def handle_message_consume_done(self, task: asyncio.Task[Any]) -> None:
        self.consumer.tasks.discard(task)
        if task.cancelled():
            return

        if (error := task.exception()) is not None:
            logger.exception("Error processing message", exc_info=error)

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

        handler_data = self.consumer.incoming_map.get(routing_key)

        if handler_data is None:
            logger.warning("No handler found for topic '%s'" % routing_key)
            await self.handle_reject_message(aio_pika_message)

            return

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
    def __init__(self, app: Microservice, config: AioPikaWorkerConfig) -> None:
        self.app = app
        self.config = config
        self.container = Container(app)
        self.lifecycle = AppLifecycle(app, self.container)

        self.uow_context_provider = UnitOfWorkContextProvider(
            app=app, container=self.container
        )

        self._consumer: AioPikaMicroserviceConsumer | None = None

    @property
    def consumer(self) -> AioPikaMicroserviceConsumer:
        if self._consumer is None:
            raise RuntimeError("Consumer not started")
        return self._consumer

    async def start_async(self, handler_names: set[str] | None = None) -> None:
        all_message_handlers_set: MESSAGE_HANDLER_DATA_SET = set()
        async with self.lifecycle():
            for instance_type in self.app.controllers:
                controller = MessageBusController.get_messagebus(instance_type)

                if controller is None:
                    continue

                instance: Any = self.container.get_by_type(instance_type)

                factory = controller.get_messagebus_factory()
                handlers, _ = factory(instance)

                message_handler_data_map: dict[str, MessageHandlerData] = {}

                for handler_data in handlers:
                    message_type = handler_data.spec.message_type
                    topic = message_type.MESSAGE_TOPIC

                    # Filter handlers by name if specified
                    if handler_names is not None and handler_data.spec.name is not None:
                        if handler_data.spec.name not in handler_names:
                            continue
                    elif handler_names is not None and handler_data.spec.name is None:
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

            consumer = self._consumer = AioPikaMicroserviceConsumer(
                config=self.config,
                message_handler_set=all_message_handlers_set,
                uow_context_provider=self.uow_context_provider,
            )

            await consumer.consume()

    def start_sync(self, handler_names: set[str] | None = None) -> None:

        def on_shutdown(loop: asyncio.AbstractEventLoop) -> None:
            logger.info("Shutting down")
            self.consumer.shutdown_event.set()

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.get_loop().add_signal_handler(
                signal.SIGINT, on_shutdown, runner.get_loop()
            )
            runner.run(self.start_async(handler_names=handler_names))


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
