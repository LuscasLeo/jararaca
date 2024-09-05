import asyncio
import inspect
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncContextManager, Callable, Type, get_origin

import aio_pika
import uvloop
from pydantic import BaseModel

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.messagebus import Message
from jararaca.messagebus.decorators import MESSAGEBUS_INCOMING_MAP, MessageBusController
from jararaca.microservice import Microservice


@dataclass
class AioPikaConfig:
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


class AioPikaMicroserviceProvider:
    def __init__(
        self,
        config: AioPikaConfig,
        incoming_map: MESSAGEBUS_INCOMING_MAP,
        uow_context_provider: Callable[..., AsyncContextManager[None]],
    ):
        self.config = config
        self.incoming_map = incoming_map
        self.uow_context_provider = uow_context_provider

    def start_consumer(self) -> None:

        async def consume() -> None:
            connection = await aio_pika.connect_robust(self.config.url)

            channel = await connection.channel()

            await channel.set_qos(prefetch_count=self.config.prefetch_count)

            topic_exchange = await channel.declare_exchange(
                "%s_topic" % self.config.exchange, type="topic"
            )

            queue = await channel.declare_queue(self.config.queue)

            topics = [*self.incoming_map.keys()]

            for topic in topics:
                await queue.bind(topic_exchange, routing_key=topic)

            await queue.consume(self.on_message)

            try:
                # Wait until terminate
                await asyncio.Future()
            finally:
                await connection.close()

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(consume())

    # TODO: Apply Unit of Work Pattern
    async def on_message(
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

        try:
            async with self.uow_context_provider():
                await handler(builded_message)
        except:
            logging.exception("deu pau")
        finally:
            await aio_pika_message.ack()


def create_messagebus_worker(app: Microservice) -> None:
    container = Container(app)

    combined_messagebus_incoming_map: MESSAGEBUS_INCOMING_MAP = {}

    uow_context_provider = asynccontextmanager(UnitOfWorkContextProvider(app))

    for instance_type in app.controllers:
        controller = MessageBusController.get_messagebus(instance_type)

        if controller is None:
            continue

        instance: Any = container.get_by_type(instance_type)

        factory = controller.get_messagebus_factory()(instance)

        for topic, handler in factory.items():
            if topic in combined_messagebus_incoming_map:
                logging.warning(
                    "Topic '%s' already registered by another controller" % topic
                )
            combined_messagebus_incoming_map[topic] = handler

        factory

    AioPikaMicroserviceProvider(
        config=AioPikaConfig(
            url="amqp://guest:guest@localhost/",
            exchange="test_exchange",
            queue="test_queue",
            prefetch_count=1,
        ),
        incoming_map=combined_messagebus_incoming_map,
        uow_context_provider=uow_context_provider,
    ).start_consumer()
