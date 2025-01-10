from typing import ClassVar, Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel

from jararaca.messagebus.publisher import use_publisher


class Message(BaseModel):
    """
    Base class for messages representing tasks.
    A Task is a message that represents a unit of work to be done.
    It is published to a TaskPublisher and consumed by a TaskHandler, wrapped in TaskData.
    Note: A Task is not an Event.
    """

    MESSAGE_TOPIC: ClassVar[str] = "__unset__"

    MESSAGE_TYPE: ClassVar[Literal["task", "event"]] = "task"

    async def publish(self) -> None:
        task_publisher = use_publisher()
        await task_publisher.publish(self, self.MESSAGE_TOPIC)


INHERITS_MESSAGE_CO = TypeVar("INHERITS_MESSAGE_CO", bound=Message, covariant=True)


class MessageOf(Protocol, Generic[INHERITS_MESSAGE_CO]):

    def payload(self) -> INHERITS_MESSAGE_CO: ...
