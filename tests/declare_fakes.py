# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A fake RabbitMQ connection recording every declaration the `declare` command
makes, plus a test application importable as a real module."""

import json
import sys
import textwrap
from pathlib import Path
from typing import Any

TEST_APP_MODULE = "jararaca_declare_testapp"

TEST_APP_SOURCE = textwrap.dedent("""
    from jararaca import Microservice
    from jararaca.messagebus import MessageOf
    from jararaca.messagebus.decorators import MessageBusController, MessageHandler
    from jararaca.messagebus.message import Message
    from jararaca.scheduler.decorators import ScheduledAction


    class OrderPlaced(Message):
        MESSAGE_TOPIC = "orders.placed"

        order_id: str


    class InvoiceIssued(Message):
        MESSAGE_TOPIC = "invoices.issued"

        invoice_id: str


    @MessageBusController()
    class OrdersController:
        @MessageHandler(OrderPlaced)
        async def on_order(self, message: MessageOf[OrderPlaced]) -> None: ...

        @ScheduledAction("* * * * *")
        async def sweep(self) -> None: ...

        # Both decorators on one member: `declare` makes a queue for each.
        @MessageHandler(InvoiceIssued)
        @ScheduledAction("0 * * * *")
        async def both(self, message: MessageOf[InvoiceIssued]) -> None: ...


    @MessageBusController()
    class BillingController:
        @MessageHandler(InvoiceIssued, group="billing")
        async def on_invoice(self, message: MessageOf[InvoiceIssued]) -> None: ...


    class NotAMessageBusController:
        pass


    app = Microservice(
        providers=[],
        controllers=[OrdersController, BillingController, NotAMessageBusController],
        interceptors=[],
    )
""")


def write_test_app(directory: Path) -> str:
    """Write the test app as an importable module and return its app path."""
    (directory / f"{TEST_APP_MODULE}.py").write_text(TEST_APP_SOURCE)
    return f"{TEST_APP_MODULE}:app"


def forget_test_app() -> None:
    sys.modules.pop(TEST_APP_MODULE, None)


class DeclarationFailed(Exception):
    """Stands for the broker refusing a declaration (PRECONDITION_FAILED)."""


class FakeBroker:
    """
    Records the operations of every channel opened on the fake connection.

    `infra_exists` makes the passive declarations of the main exchange, the dead
    letter exchange and queue succeed. Names in `fail_once` refuse their first
    non-passive declaration, as a broker does on an arguments mismatch.
    """

    def __init__(
        self, infra_exists: bool = False, fail_once: set[str] | None = None
    ) -> None:
        self.infra_exists = infra_exists
        self.fail_once = set(fail_once or ())
        self.channels: list[list[list[Any]]] = []
        self.connected_urls: list[str] = []

    async def connect(self, url: str, **kwargs: Any) -> "FakeConnection":
        self.connected_urls.append(url)
        return FakeConnection(self)

    def _maybe_fail(self, name: str, passive: bool) -> None:
        if passive:
            if not self.infra_exists:
                raise DeclarationFailed(f"NOT_FOUND - {name}")
            return
        if name in self.fail_once:
            self.fail_once.discard(name)
            raise DeclarationFailed(f"PRECONDITION_FAILED - {name}")

    def normalized(self) -> dict[str, Any]:
        """
        Infrastructure channels in the order they ran, then one entry per queue
        channel in name order, since queue declarations are independent.
        """

        def is_app_queue_channel(ops: list[list[Any]]) -> bool:
            return any(op[0] == "declare_queue" and op[1] != "dlq" for op in ops)

        infra = [ops for ops in self.channels if not is_app_queue_channel(ops)]
        queues = sorted(
            (ops for ops in self.channels if is_app_queue_channel(ops)),
            key=lambda ops: json.dumps(ops),
        )
        return {"infrastructure": infra, "queues": queues}


class FakeExchange:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeQueue:
    def __init__(self, name: str, ops: list[list[Any]]) -> None:
        self.name = name
        self._ops = ops

    async def bind(self, exchange: Any, routing_key: str | None = None) -> None:
        exchange_name = exchange if isinstance(exchange, str) else exchange.name
        self._ops.append(["bind", self.name, exchange_name, routing_key])


class FakeChannel:
    def __init__(self, broker: FakeBroker) -> None:
        self.broker = broker
        self.ops: list[list[Any]] = []
        broker.channels.append(self.ops)

    async def __aenter__(self) -> "FakeChannel":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def declare_exchange(
        self,
        name: str,
        type: str = "direct",
        *,
        durable: bool = False,
        auto_delete: bool = False,
        passive: bool = False,
        **kwargs: Any,
    ) -> FakeExchange:
        self.ops.append(["declare_exchange", name, type, durable, auto_delete, passive])
        self.broker._maybe_fail(name, passive)
        return FakeExchange(name)

    async def declare_queue(
        self,
        name: str | None = None,
        *,
        durable: bool = False,
        passive: bool = False,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> FakeQueue:
        assert name is not None
        self.ops.append(
            [
                "declare_queue",
                name,
                durable,
                passive,
                sorted((arguments or {}).items()),
            ]
        )
        self.broker._maybe_fail(name, passive)
        return FakeQueue(name, self.ops)

    async def exchange_delete(self, exchange_name: str, **kwargs: Any) -> None:
        self.ops.append(["exchange_delete", exchange_name])

    async def queue_delete(self, queue_name: str, **kwargs: Any) -> None:
        self.ops.append(["queue_delete", queue_name])


class FakeConnection:
    def __init__(self, broker: FakeBroker) -> None:
        self.broker = broker

    def channel(self) -> FakeChannel:
        return FakeChannel(self.broker)

    async def close(self) -> None:
        return None
