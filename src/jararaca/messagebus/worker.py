import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aio_pika
import uvloop

from jararaca.di import Container
from jararaca.messagebus.decorators import MESSAGEBUS_INCOMING_MAP, MessageBusController
from jararaca.microservice import Microservice


@dataclass
class AioPikaConfig:
    url: str
    queue: str
    exchange: str


class AioPikaMicroserviceProvider:
    def __init__(self, config: AioPikaConfig, incoming_map: MESSAGEBUS_INCOMING_MAP):
        self.config = config
        self.incoming_map = incoming_map

    def start_consumer(self) -> None:

        async def consume() -> None:
            connection = await aio_pika.connect_robust(self.config.url)

            channel = await connection.channel()

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

            # async with queue.iterator() as queue_iter:

            #     async for message in queue_iter:

            #         async with message.process():
            #             topic = message.routing_key

            #             handler = self.incoming_map.get(topic)

            #             if handler is None:
            #                 logging.warning("No handler found for topic '%s'" % topic)
            #                 continue

            #             try:
            #                 await handler(message)
            #             except:
            #                 logging.exception("deu pau")
            #             finally:
            #                 await message.ack()

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(consume())

    async def on_message(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        topic = message.routing_key

        if topic is None:
            logging.warning("No topic found for message")

            return

        handler = self.incoming_map.get(topic)

        if handler is None:
            logging.warning("No handler found for topic '%s'" % topic)
            return

        try:
            await handler(message)  # type: ignore[arg-type]  # TODO: Transform message from aio_pika to a more generic message
        except:
            logging.exception("deu pau")
        finally:
            await message.ack()


def create_messagebus_worker(app: Microservice) -> None:
    container = Container(app)

    combined_messagebus_incoming_map: MESSAGEBUS_INCOMING_MAP = {}

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
        ),
        incoming_map=combined_messagebus_incoming_map,
    ).start_consumer()
