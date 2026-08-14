# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import contextmanager, suppress
from contextvars import ContextVar
from typing import Any, Generator, Literal, NoReturn, Protocol

MessageDisposition = Literal["ack", "nack", "reject", "retry"]


class MessageDisposed(BaseException):
    """
    Unwinds the handler after it decided the fate of the message itself.

    `retry`, `retry_later`, `nack` and `reject` hand the message back to the broker (or
    schedule a fresh copy of it), so any code that keeps running afterwards is racing
    against a redelivery of the very same message — potentially in another worker,
    concurrently. Raising stops the handler at the point of decision instead.

    It derives from `BaseException` on purpose: a broad `except Exception` around
    business logic must not silently turn a "give this message back" decision into
    "keep going".
    """

    def __init__(self, disposition: MessageDisposition) -> None:
        self.disposition: MessageDisposition = disposition
        super().__init__(f"Message disposed by the handler ({disposition})")


class BusMessageController(Protocol):

    @property
    def disposition(self) -> MessageDisposition | None:
        """The disposition applied by the handler, or None while still undecided."""
        ...

    async def ack(self) -> None:
        pass

    async def nack(self) -> NoReturn:
        raise MessageDisposed("nack")

    async def reject(self) -> NoReturn:
        raise MessageDisposed("reject")

    async def retry(self) -> NoReturn:
        raise MessageDisposed("retry")

    async def retry_later(self, delay: int) -> NoReturn:
        raise MessageDisposed("retry")


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


async def nack() -> NoReturn:
    controller = use_bus_message_controller()
    await controller.nack()


async def reject() -> NoReturn:
    controller = use_bus_message_controller()
    await controller.reject()


async def retry() -> NoReturn:
    controller = use_bus_message_controller()
    await controller.retry()


async def retry_later(delay: int) -> NoReturn:
    controller = use_bus_message_controller()
    await controller.retry_later(delay)
