from contextlib import asynccontextmanager
from typing import AsyncContextManager, AsyncGenerator, Protocol

from jararaca.broker_backend import MessageBrokerBackend
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
    ):
        self.connection_factory = connection_factory
        self.connection_name = connection_name
        self.message_scheduler = message_scheduler

    @asynccontextmanager
    async def intercept(
        self, app_context: AppTransactionContext
    ) -> AsyncGenerator[None, None]:
        if app_context.transaction_data.context_type == "websocket":
            yield
            return

        async with self.connection_factory.provide_connection() as connection:
            with provide_message_publisher(self.connection_name, connection):
                yield

                await connection.flush()
