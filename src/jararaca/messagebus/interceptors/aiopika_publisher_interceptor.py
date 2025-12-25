# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from datetime import tzinfo as _TzInfo
from typing import Any, AsyncGenerator

import aio_pika
import tenacity
from aio_pika.abc import AbstractConnection
from pydantic import BaseModel

from jararaca.broker_backend import MessageBrokerBackend
from jararaca.messagebus import implicit_headers
from jararaca.messagebus.interceptors.publisher_interceptor import (
    MessageBusConnectionFactory,
)
from jararaca.messagebus.publisher import IMessage, MessagePublisher
from jararaca.scheduler.types import DelayedMessageData

logger = logging.getLogger(__name__)


class AIOPikaMessagePublisher(MessagePublisher):

    def __init__(
        self,
        channel: aio_pika.abc.AbstractChannel,
        exchange_name: str,
        message_broker_backend: MessageBrokerBackend | None = None,
    ):

        self.channel = channel
        self.exchange_name = exchange_name
        self.message_broker_backend = message_broker_backend
        self.staged_delayed_messages: list[DelayedMessageData] = []
        self.staged_messages: list[IMessage] = []

    async def publish(self, message: IMessage, topic: str) -> None:
        self.staged_messages.append(message)

    async def _publish(self, message: IMessage, topic: str) -> None:
        exchange = await self.channel.get_exchange(self.exchange_name, ensure=False)
        if not exchange:
            logging.warning(f"Exchange {self.exchange_name} not found")
            return
        routing_key = f"{topic}.#"

        implicit_headers_data = implicit_headers.use_implicit_headers()
        await exchange.publish(
            aio_pika.Message(
                body=message.model_dump_json().encode(), headers=implicit_headers_data
            ),
            routing_key=routing_key,
        )

    async def delay(self, message: IMessage, seconds: int) -> None:
        if not self.message_broker_backend:
            raise NotImplementedError(
                "Delay is not implemented for AIOPikaMessagePublisher"
            )
        self.staged_delayed_messages.append(
            DelayedMessageData(
                message_topic=message.MESSAGE_TOPIC,
                payload=message.model_dump_json().encode(),
                dispatch_time=int(
                    (datetime.now(tz=None) + timedelta(seconds=seconds)).timestamp()
                ),
            )
        )

    async def schedule(
        self, message: IMessage, when: datetime, timezone: _TzInfo
    ) -> None:
        if not self.message_broker_backend:
            raise NotImplementedError(
                "Schedule is not implemented for AIOPikaMessagePublisher"
            )
        self.staged_delayed_messages.append(
            DelayedMessageData(
                message_topic=message.MESSAGE_TOPIC,
                payload=message.model_dump_json().encode(),
                dispatch_time=int(when.timestamp()),
            )
        )

    async def flush(self) -> None:
        for message in self.staged_messages:
            logger.debug(
                f"Publishing message {message.MESSAGE_TOPIC} with payload: {message.model_dump_json()}"
            )
            await self._publish(message, message.MESSAGE_TOPIC)

        if len(self.staged_delayed_messages) > 0:
            if not self.message_broker_backend:
                raise NotImplementedError(
                    "MessageBrokerBackend is required to publish delayed messages"
                )

            for delayed_message in self.staged_delayed_messages:
                logger.debug(
                    f"Scheduling delayed message {delayed_message.message_topic} with payload: {delayed_message.payload.decode()}"
                )
                await self.message_broker_backend.enqueue_delayed_message(
                    delayed_message
                )


class GenericPoolConfig(BaseModel):
    max_size: int


default_retry = tenacity.retry(
    wait=tenacity.wait_exponential_jitter(initial=1, max=60),
    before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
)

default_retry_channel = tenacity.retry(
    wait=tenacity.wait_exponential_jitter(initial=1, max=60),
    before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
    stop=tenacity.stop_after_attempt(5),
)


class AIOPikaConnectionFactory(MessageBusConnectionFactory):

    def __init__(
        self,
        url: str,
        exchange: str,
        connection_pool_config: GenericPoolConfig | None = None,
        channel_pool_config: GenericPoolConfig | None = None,
        message_broker_backend: MessageBrokerBackend | None = None,
    ):
        self.url = url
        self.exchange = exchange
        self.message_broker_backend = message_broker_backend
        self.connection_pool: aio_pika.pool.Pool[AbstractConnection] | None = None
        self.channel_pool: aio_pika.pool.Pool[aio_pika.abc.AbstractChannel] | None = (
            None
        )

        if connection_pool_config:

            @default_retry
            async def get_connection() -> AbstractConnection:
                return await aio_pika.connect(self.url)

            self.connection_pool = aio_pika.pool.Pool[AbstractConnection](
                get_connection,
                max_size=connection_pool_config.max_size,
            )

        if channel_pool_config:

            @default_retry_channel
            async def get_channel() -> aio_pika.abc.AbstractChannel:
                async with self.acquire_connection() as connection:
                    return await connection.channel(publisher_confirms=False)

            self.channel_pool = aio_pika.pool.Pool[aio_pika.abc.AbstractChannel](
                get_channel, max_size=channel_pool_config.max_size
            )

    @default_retry
    async def _connect(self) -> AbstractConnection:
        return await aio_pika.connect(self.url)

    @asynccontextmanager
    async def acquire_connection(self) -> AsyncGenerator[AbstractConnection, Any]:
        if not self.connection_pool:
            async with await self._connect() as connection:
                yield connection
        else:

            async with self.connection_pool.acquire() as connection:
                yield connection

    @asynccontextmanager
    async def acquire_channel(
        self,
    ) -> AsyncGenerator[aio_pika.abc.AbstractChannel, Any]:

        async for attempt in tenacity.AsyncRetrying(
            wait=tenacity.wait_exponential_jitter(initial=1, max=10),
            before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
            # stop=tenacity.stop_after_attempt(9000),
        ):
            with attempt:

                if not self.connection_pool or not self.channel_pool:
                    async with self.acquire_connection() as connection:
                        yield await connection.channel(publisher_confirms=False)
                else:

                    async with self.connection_pool.acquire() as connection:
                        if not connection.connected.is_set():
                            await connection.connect()

                        async with connection.channel(
                            publisher_confirms=False
                        ) as channel:
                            yield channel
                        # await connection.close()
                if attempt.retry_state.attempt_number > 1:
                    logger.warning(
                        "Later successful connection attempt #%d",
                        attempt.retry_state.attempt_number,
                    )

    @asynccontextmanager
    async def provide_connection(self) -> AsyncGenerator[AIOPikaMessagePublisher, Any]:

        async with self.acquire_channel() as channel:
            tx = channel.transaction()

            await tx.select()

            try:
                yield AIOPikaMessagePublisher(
                    channel,
                    exchange_name=self.exchange,
                    message_broker_backend=self.message_broker_backend,
                )
                await tx.commit()
            except Exception as e:
                await tx.rollback()
                raise e
