from abc import ABC
from contextlib import asynccontextmanager
from typing import AsyncContextManager, AsyncGenerator, Iterable

from jararaca.scheduler.types import DelayedMessageData


class MessageBrokerBackend(ABC):

    def lock(self) -> AsyncContextManager[None]:
        """
        Acquire a lock for the message broker backend.
        This is used to ensure that only one instance of the scheduler is running at a time.
        """
        raise NotImplementedError(f"lock() is not implemented by {self.__class__}.")

    async def get_last_dispatch_time(self, action_name: str) -> int | None:
        """
        Get the last dispatch time of the scheduled action.
        This is used to determine if the scheduled action should be executed again
        or if it should be skipped.
        """
        raise NotImplementedError(
            f"get_last_dispatch_time() is not implemented by {self.__class__}."
        )

    async def set_last_dispatch_time(self, action_name: str, timestamp: int) -> None:
        """
        Set the last dispatch time of the scheduled action.
        This is used to determine if the scheduled action should be executed again
        or if it should be skipped.
        """
        raise NotImplementedError(
            f"set_last_dispatch_time() is not implemented by {self.__class__}."
        )

    async def get_in_execution_count(self, action_name: str) -> int:
        """
        Get the number of scheduled actions in execution.
        This is used to determine if the scheduled action should be executed again
        or if it should be skipped.
        """
        raise NotImplementedError(
            f"get_in_execution_count() is not implemented by {self.__class__}."
        )

    def in_execution(self, action_name: str) -> AsyncContextManager[None]:
        """
        Acquire a lock for the scheduled action.
        This is used to ensure that only one instance of the scheduled action is running at a time.
        """
        raise NotImplementedError(
            f"in_execution() is not implemented by {self.__class__}."
        )

    async def dequeue_next_delayed_messages(
        self, start_timestamp: int
    ) -> Iterable[DelayedMessageData]:
        """
        Dequeue the next delayed messages from the message broker.
        This is used to trigger the scheduled action.
        """
        raise NotImplementedError(
            f"dequeue_next_delayed_messages() is not implemented by {self.__class__}."
        )

    async def enqueue_delayed_message(
        self, delayed_message: DelayedMessageData
    ) -> None:
        """
        Enqueue a delayed message to the message broker.
        This is used to trigger the scheduled action.
        """
        raise NotImplementedError(
            f"enqueue_delayed_message() is not implemented by {self.__class__}."
        )

    async def dispose(self) -> None:
        """
        Dispose of the message broker backend.
        This is used to clean up resources used by the message broker backend.
        """


class NullBackend(MessageBrokerBackend):
    """
    A null backend that does nothing.
    This is used for testing purposes.
    """

    @asynccontextmanager
    async def lock(self) -> AsyncGenerator[None, None]:
        yield

    async def get_last_dispatch_time(self, action_name: str) -> int:
        return 0

    async def set_last_dispatch_time(self, action_name: str, timestamp: int) -> None:
        pass

    async def dispose(self) -> None:
        pass
