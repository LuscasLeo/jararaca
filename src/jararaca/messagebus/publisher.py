from abc import ABC, abstractmethod
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import datetime, tzinfo
from typing import Any, ClassVar, Generator, Literal

from pydantic import BaseModel


class IMessage(BaseModel):
    """
    Base class for messages representing tasks.
    A Task is a message that represents a unit of work to be done.
    It is published to a TaskPublisher and consumed by a TaskHandler, wrapped in TaskData.
    Note: A Task is not an Event.
    """

    MESSAGE_TOPIC: ClassVar[str] = "__unset__"

    MESSAGE_TYPE: ClassVar[Literal["task", "event"]] = "task"


class MessagePublisher(ABC):
    @abstractmethod
    async def publish(self, message: IMessage, topic: str) -> None:
        pass

    @abstractmethod
    async def delay(self, message: IMessage, seconds: int) -> None:
        """
        Delay the message for a given number of seconds.
        """

    @abstractmethod
    async def schedule(
        self, message: IMessage, when: datetime, timezone: tzinfo
    ) -> None:
        """
        Schedule the message for a given datetime.
        """

    @abstractmethod
    async def flush(self) -> None:
        """
        Publish all messages that have been delayed or scheduled.
        This is typically called at the end of a request or task processing.
        """


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
        with suppress(ValueError):
            message_publishers_ctx.reset(token)


def use_publisher(connecton_name: str = "default") -> MessagePublisher:
    publisher = message_publishers_ctx.get({}).get(connecton_name)
    if publisher is None:
        raise ValueError(f"MessagePublisher not found for connection {connecton_name}")

    return publisher
