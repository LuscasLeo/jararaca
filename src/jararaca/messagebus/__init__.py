from typing import Generic, Protocol, TypeVar

T = TypeVar("T", covariant=True)


class Message(Protocol, Generic[T]):

    def payload(self) -> T: ...

    async def ack(self) -> None: ...

    async def reject(self) -> None: ...

    async def nack(self) -> None: ...
