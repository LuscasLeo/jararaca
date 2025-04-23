from datetime import datetime, tzinfo
from typing import Generic, Protocol, TypeVar

from jararaca.messagebus.publisher import IMessage, use_publisher


class Message(IMessage):

    async def publish(self) -> None:
        task_publisher = use_publisher()
        await task_publisher.publish(self, self.MESSAGE_TOPIC)

    async def delay(self, seconds: int) -> None:
        task_publisher = use_publisher()
        await task_publisher.delay(self, seconds)

    async def schedule(self, when: datetime, tz: tzinfo) -> None:
        task_publisher = use_publisher()
        await task_publisher.schedule(self, when, tz)


INHERITS_MESSAGE_CO = TypeVar("INHERITS_MESSAGE_CO", bound=Message, covariant=True)


class MessageOf(Protocol, Generic[INHERITS_MESSAGE_CO]):

    def payload(self) -> INHERITS_MESSAGE_CO: ...
