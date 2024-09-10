from contextlib import asynccontextmanager
from typing import Any, AsyncContextManager, AsyncGenerator, Protocol

import aio_pika
from pydantic import BaseModel

from jararaca.messagebus.publisher import MessagePublisher, provide_message_publisher
from jararaca.microservice import AppContext, AppInterceptor


class MessageBusConnectionFactory(Protocol):

    def provide_connection(self) -> AsyncContextManager[MessagePublisher]: ...


class MessageBusPublisherInterceptor(AppInterceptor):

    def __init__(
        self,
        connection_factory: MessageBusConnectionFactory,
        connection_name: str = "default",
    ):
        self.connection_factory = connection_factory
        self.connection_name = connection_name

    @asynccontextmanager
    async def intercept(self, app_context: AppContext) -> AsyncGenerator[None, None]:
        async with self.connection_factory.provide_connection() as connection:
            with provide_message_publisher(self.connection_name, connection):
                yield


class AIOPikaMessagePublisher(MessagePublisher):

    def __init__(self, channel: aio_pika.abc.AbstractChannel, exchange_name: str):
        self.channel = channel
        self.exchange_name = exchange_name

    async def publish(self, message: BaseModel, topic: str) -> None:
        exchange = await self.channel.declare_exchange(
            self.exchange_name,
            type=aio_pika.ExchangeType.TOPIC,
        )

        await exchange.publish(
            aio_pika.Message(body=message.model_dump_json().encode()),
            routing_key=topic,
        )


class AIOPikaConnectionFactory(MessageBusConnectionFactory):

    def __init__(
        self,
        url: str,
        exchange: str,
    ):
        self.url = url
        self.exchange = exchange

    @asynccontextmanager
    async def provide_connection(self) -> AsyncGenerator[AIOPikaMessagePublisher, Any]:
        connection = await aio_pika.connect_robust(self.url)
        async with connection:
            async with connection.channel(publisher_confirms=False) as channel:

                tx = channel.transaction()

                await tx.select()

                try:
                    yield AIOPikaMessagePublisher(channel, exchange_name=self.exchange)
                    await tx.commit()
                except Exception as e:
                    await tx.rollback()
                    raise e
