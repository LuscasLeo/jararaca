from contextlib import asynccontextmanager
from typing import Any, AsyncContextManager, AsyncGenerator, Protocol

import aio_pika
from aio_pika.abc import AbstractConnection
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
        if app_context.context_type == "websocket":
            yield
            return

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
        routing_key = f"{self.exchange_name}.{topic}."
        await exchange.publish(
            aio_pika.Message(body=message.model_dump_json().encode()),
            routing_key=routing_key,
        )


class GenericPoolConfig(BaseModel):
    max_size: int


class AIOPikaConnectionFactory(MessageBusConnectionFactory):

    def __init__(
        self,
        url: str,
        exchange: str,
        connection_pool_config: GenericPoolConfig | None = None,
        channel_pool_config: GenericPoolConfig | None = None,
    ):
        self.url = url
        self.exchange = exchange

        self.connection_pool: aio_pika.pool.Pool[AbstractConnection] | None = None
        self.channel_pool: aio_pika.pool.Pool[aio_pika.abc.AbstractChannel] | None = (
            None
        )

        if connection_pool_config:

            async def get_connection() -> AbstractConnection:
                return await aio_pika.connect_robust(self.url)

            self.connection_pool = aio_pika.pool.Pool[AbstractConnection](
                get_connection,
                max_size=connection_pool_config.max_size,
            )

        if channel_pool_config:

            async def get_channel() -> aio_pika.abc.AbstractChannel:
                async with self.acquire_connection() as connection:
                    return await connection.channel(publisher_confirms=False)

            self.channel_pool = aio_pika.pool.Pool[aio_pika.abc.AbstractChannel](
                get_channel, max_size=channel_pool_config.max_size
            )

    @asynccontextmanager
    async def acquire_connection(self) -> AsyncGenerator[AbstractConnection, Any]:
        if not self.connection_pool:
            async with await aio_pika.connect_robust(self.url) as connection:
                yield connection
        else:

            async with self.connection_pool.acquire() as connection:
                yield connection

    @asynccontextmanager
    async def acquire_channel(
        self,
    ) -> AsyncGenerator[aio_pika.abc.AbstractChannel, Any]:
        if not self.channel_pool:
            async with self.acquire_connection() as connection:
                yield await connection.channel(publisher_confirms=False)
        else:
            async with self.channel_pool.acquire() as channel:
                yield channel

    @asynccontextmanager
    async def provide_connection(self) -> AsyncGenerator[AIOPikaMessagePublisher, Any]:

        async with self.acquire_channel() as channel:
            tx = channel.transaction()

            await tx.select()

            try:
                yield AIOPikaMessagePublisher(channel, exchange_name=self.exchange)
                await tx.commit()
            except Exception as e:
                await tx.rollback()
                raise e
