from typing import Generic, Protocol, TypeVar

T = TypeVar("T", covariant=True)


class Message(Protocol, Generic[T]):

    id: str

    def payload(self) -> T: ...
