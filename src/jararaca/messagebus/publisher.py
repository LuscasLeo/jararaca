from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator, Protocol

from pydantic import BaseModel


class MessagePublisher(Protocol):
    async def publish(self, message: BaseModel, topic: str) -> None:
        raise NotImplementedError()


message_publishers_ctx = ContextVar[dict[str, MessagePublisher]](
    "message_publishers_ctx", default={}
)


@contextmanager
def provide_message_publisher(
    connection_name: str, message_publisher: MessagePublisher
) -> Generator[None, Any, None]:

    current_map = message_publishers_ctx.get({})

    token = message_publishers_ctx.set(
        {**current_map, connection_name: message_publisher}
    )

    try:
        yield
    finally:
        try:
            message_publishers_ctx.reset(token)
        except ValueError:
            pass


def use_publisher(connecton_name: str = "default") -> MessagePublisher:
    publisher = message_publishers_ctx.get({}).get(connecton_name)
    if publisher is None:
        raise ValueError(f"MessagePublisher not found for connection {connecton_name}")

    return publisher
