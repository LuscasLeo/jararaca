# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import asynccontextmanager
from typing import AsyncContextManager, AsyncGenerator, Protocol

from jararaca.broker_backend import MessageBrokerBackend
from jararaca.messagebus.interceptors.message_publisher_collector import (
    MessagePublisherCollector,
)
from jararaca.messagebus.publisher import MessagePublisher, provide_message_publisher
from jararaca.microservice import AppInterceptor, AppTransactionContext


class MessageBusConnectionFactory(Protocol):

    def provide_connection(self) -> AsyncContextManager[MessagePublisher]: ...


class MessageBusPublisherInterceptor(AppInterceptor):

    def __init__(
        self,
        connection_factory: MessageBusConnectionFactory,
        connection_name: str = "default",
        message_scheduler: MessageBrokerBackend | None = None,
        *,
        open_connection_at_end_of_transaction: bool = False,
    ):
        self.connection_factory = connection_factory
        self.connection_name = connection_name
        self.message_scheduler = message_scheduler
        self.open_connection_at_end_of_transaction = (
            open_connection_at_end_of_transaction
        )

    @asynccontextmanager
    async def intercept(
        self, app_context: AppTransactionContext
    ) -> AsyncGenerator[None, None]:
        if app_context.transaction_data.context_type == "websocket":
            yield
            return

        if self.open_connection_at_end_of_transaction:

            collector = MessagePublisherCollector()
            with provide_message_publisher(self.connection_name, collector):
                yield

            if collector.has_messages():
                async with self.connection_factory.provide_connection() as connection:
                    await collector.fill(connection)
                    await connection.flush()
                    return
        else:

            yield

            async with self.connection_factory.provide_connection() as connection:
                with provide_message_publisher(self.connection_name, connection):

                    await connection.flush()
