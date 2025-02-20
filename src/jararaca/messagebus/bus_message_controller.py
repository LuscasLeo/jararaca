from contextlib import contextmanager, suppress
from contextvars import ContextVar
from typing import Any, Generator, Protocol


class BusMessageController(Protocol):

    async def ack(self) -> None:
        pass

    async def nack(self) -> None:
        pass

    async def reject(self) -> None:
        pass

    async def retry(self) -> None:
        pass

    async def retry_later(self, delay: int) -> None:
        pass


bus_message_controller_ctxvar = ContextVar[BusMessageController](
    "bus_message_controller"
)


@contextmanager
def provide_bus_message_controller(
    controller: BusMessageController,
) -> Generator[None, Any, None]:
    token = bus_message_controller_ctxvar.set(controller)
    try:
        yield
    finally:
        with suppress(LookupError):
            bus_message_controller_ctxvar.reset(token)


def use_bus_message_controller() -> BusMessageController:
    return bus_message_controller_ctxvar.get()


async def ack() -> None:
    controller = use_bus_message_controller()
    await controller.ack()


async def nack() -> None:
    controller = use_bus_message_controller()
    await controller.nack()


async def reject() -> None:
    controller = use_bus_message_controller()
    await controller.reject()


async def retry() -> None:
    controller = use_bus_message_controller()
    await controller.retry()


async def retry_later(delay: int) -> None:
    controller = use_bus_message_controller()
    await controller.retry_later(delay)
