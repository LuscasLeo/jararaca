import asyncio
import inspect
import logging
import signal
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncContextManager, AsyncGenerator, Type, get_origin

import aio_pika
import uvloop
from pydantic import BaseModel

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.lifecycle import AppLifecycle
from jararaca.messagebus import Message
from jararaca.messagebus.decorators import (
    MESSAGEBUS_INCOMING_MAP,
    IncomingHandler,
    MessageBusController,
)
from jararaca.microservice import MessageBusAppContext, Microservice

logger = logging.getLogger(__name__)


@dataclass
class AioPikaWorkerConfig:
    url: str
    queue: str
    exchange: str
    prefetch_count: int


class AioPikaMessage(Message[BaseModel]):

    def __init__(
        self,
        aio_pika_message: aio_pika.abc.AbstractIncomingMessage,
        model_type: Type[BaseModel],
    ):
        self.aio_pika_message = aio_pika_message
        self.model_type = model_type

    def payload(self) -> BaseModel:
        return self.model_type.model_validate_json(self.aio_pika_message.body)

    async def ack(self) -> None:
        await self.aio_pika_message.ack()

    async def reject(self) -> None:
        await self.aio_pika_message.reject()

    async def nack(self) -> None:
        await self.aio_pika_message.nack()


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
        incoming_map: MESSAGEBUS_INCOMING_MAP,
        uow_context_provider: UnitOfWorkContextProvider,
    ):
        self.config = config
        self.incoming_map = incoming_map
        self.uow_context_provider = uow_context_provider
        self.shutdown_event = asyncio.Event()
        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task[Any]] = set()

    async def consume(self) -> None:

        connection = await aio_pika.connect_robust(self.config.url)

        channel = await connection.channel()

        await channel.set_qos(prefetch_count=self.config.prefetch_count)

        topic_exchange = await channel.declare_exchange(
            self.config.exchange, type="topic"
        )

        queue = await channel.declare_queue(self.config.queue)

        topics = [*self.incoming_map.keys()]

        for topic in topics:
            await queue.bind(topic_exchange, routing_key=topic)

        await queue.consume(self.message_consumer)

        await self.shutdown_event.wait()
        logger.info("Worker shutting down")

        async with self.lock:
            logger.info("Stopping task incoming")

        logger.info("Waiting for all messages to be processed")
        await asyncio.gather(*self.tasks, return_exceptions=True)

        await channel.close()
        await connection.close()

    async def message_consumer(
        self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage
    ) -> None:
        if self.shutdown_event.is_set():
            return

        await self.process_message(aio_pika_message)

    async def process_message(
        self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage
    ) -> None:
        async with self.lock:
            task = asyncio.create_task(self.handle_message(aio_pika_message))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def handle_message(
        self, aio_pika_message: aio_pika.abc.AbstractIncomingMessage
    ) -> None:

        topic = aio_pika_message.routing_key

        if topic is None:
            logging.warning("No topic found for message")

            return

        handler = self.incoming_map.get(topic)

        if handler is None:
            logging.warning("No handler found for topic '%s'" % topic)
            return

        sig = inspect.signature(handler)

        if len(sig.parameters) != 1:
            logging.warning(
                "Handler for topic '%s' must have exactly one parameter" % topic
            )
            return

        parameter = list(sig.parameters.values())[0]

        param_origin = get_origin(parameter.annotation)

        if param_origin is not Message:
            logging.warning(
                "Handler for topic '%s' must have exactly one parameter of type Message"
                % topic
            )
            return

        if len(parameter.annotation.__args__) != 1:
            logging.warning(
                "Handler for topic '%s' must have exactly one parameter of type Message"
                % topic
            )
            return

        message_type = parameter.annotation.__args__[0]

        if not issubclass(message_type, BaseModel):
            logging.warning(
                "Handler for topic '%s' must have exactly one parameter of type Message[BaseModel]"
                % topic
            )
            return

        builded_message = AioPikaMessage(aio_pika_message, message_type)

        incoming_message_spec = IncomingHandler.get_message_incoming(handler)
        assert incoming_message_spec is not None

        async with self.uow_context_provider(
            MessageBusAppContext(
                message=builded_message,
                topic=topic,
            )
        ):
            ctx: AsyncContextManager[Any]
            if incoming_message_spec.timeout is not None:
                ctx = asyncio.timeout(incoming_message_spec.timeout)
            else:
                ctx = none_context()
            async with ctx:
                try:
                    await handler(builded_message)
                    if incoming_message_spec.auto_ack:
                        await aio_pika_message.ack()
                except BaseException as base_exc:
                    if incoming_message_spec.exception_handler is not None:
                        try:
                            incoming_message_spec.exception_handler(base_exc)
                        except Exception as nested_exc:
                            logging.exception(
                                f"Error processing exception handler: {base_exc} | {nested_exc}"
                            )

                    if incoming_message_spec.nack_on_exception:
                        await aio_pika_message.nack()
                    else:
                        await aio_pika_message.reject(requeue=False)
                else:
                    logging.info("Message processed successfully")


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

    async def start_async(self) -> None:
        combined_messagebus_incoming_map: MESSAGEBUS_INCOMING_MAP = {}
        async with self.lifecycle():
            for instance_type in self.app.controllers:
                controller = MessageBusController.get_messagebus(instance_type)

                if controller is None:
                    continue

                instance: Any = self.container.get_by_type(instance_type)

                factory = controller.get_messagebus_factory()(instance)

                for topic, consumer_func in factory.items():
                    if topic in combined_messagebus_incoming_map:
                        logging.warning(
                            "Topic '%s' already registered by another controller"
                            % topic
                        )
                    combined_messagebus_incoming_map[topic] = consumer_func

            consumer = self._consumer = AioPikaMicroserviceConsumer(
                config=self.config,
                incoming_map=combined_messagebus_incoming_map,
                uow_context_provider=self.uow_context_provider,
            )

            await consumer.consume()

    def start_sync(self) -> None:

        def on_shutdown(loop: asyncio.AbstractEventLoop) -> None:
            logging.info("Shutting down")
            self.consumer.shutdown_event.set()

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.get_loop().add_signal_handler(
                signal.SIGINT, on_shutdown, runner.get_loop()
            )
            runner.run(self.start_async())
