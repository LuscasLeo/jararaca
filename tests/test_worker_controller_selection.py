# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A worker filtered by --groups/--handlers must not instantiate controllers that
have nothing it runs, and must consume exactly the same queues as before."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from jararaca.messagebus import MessageOf
from jararaca.messagebus.aiopika import gen_queue_name
from jararaca.messagebus.decorators import (
    MESSAGE_HANDLER_DATA_SET,
    SCHEDULED_ACTION_DATA_SET,
    MessageBusController,
    MessageHandler,
)
from jararaca.messagebus.message import Message
from jararaca.messagebus.worker import MessageBusWorker
from jararaca.microservice import Microservice
from jararaca.scheduler.decorators import ScheduledAction

instantiated: list[str] = []


class OrderPlaced(Message):
    MESSAGE_TOPIC = "orders.placed"

    order_id: str


class InvoiceIssued(Message):
    MESSAGE_TOPIC = "invoices.issued"

    invoice_id: str


@MessageBusController()
class GroupAController:
    def __init__(self) -> None:
        instantiated.append("A")

    @MessageHandler(OrderPlaced, group="A", name="orders")
    async def on_order(self, message: MessageOf[OrderPlaced]) -> None: ...

    @ScheduledAction("* * * * *", group="A", name="sweep")
    async def sweep(self) -> None: ...

    @MessageHandler(InvoiceIssued, group="B", name="invoices-in-a")
    async def on_invoice(self, message: MessageOf[InvoiceIssued]) -> None: ...


@MessageBusController()
class GroupBController:
    def __init__(self) -> None:
        instantiated.append("B")

    @MessageHandler(InvoiceIssued, group="B", name="invoices")
    async def on_invoice(self, message: MessageOf[InvoiceIssued]) -> None: ...

    @ScheduledAction("0 * * * *", group="B")
    async def hourly(self) -> None: ...


class PlainController:
    """Not a message bus controller: never instantiated by the worker."""

    def __init__(self) -> None:
        instantiated.append("plain")


CONTROLLERS: list[type] = [GroupAController, GroupBController, PlainController]


def queues(
    handlers: MESSAGE_HANDLER_DATA_SET, schedulers: SCHEDULED_ACTION_DATA_SET
) -> set[str]:
    return {gen_queue_name(h.message_type, h.instance_callable) for h in handlers} | {
        f"{s.callable.__module__}.{s.callable.__qualname__}" for s in schedulers
    }


def queues_selected_after_instantiating_everything(
    handler_names: set[str] | None, handler_groups: set[str] | None
) -> set[str]:
    """The previous selection: instantiate every controller, then filter."""
    handlers: MESSAGE_HANDLER_DATA_SET = set()
    schedulers: SCHEDULED_ACTION_DATA_SET = set()

    def selected(spec: Any) -> bool:
        if handler_names is not None and spec.name is not None:
            if spec.name not in handler_names:
                return False
        elif handler_names is not None and spec.name is None:
            return False
        if handler_groups is not None and spec.group not in handler_groups:
            return False
        return True

    for controller_type in CONTROLLERS:
        controller = MessageBusController.get_last(controller_type)
        if controller is None:
            continue
        all_handlers, all_schedulers = controller.get_messagebus_factory()(
            controller_type()
        )
        handlers |= {h for h in all_handlers if selected(h.spec)}
        schedulers |= {s for s in all_schedulers if selected(s.spec)}
    return queues(handlers, schedulers)


async def run_worker(
    handler_names: set[str] | None = None, handler_groups: set[str] | None = None
) -> set[str]:
    """Run the worker startup up to the consumer and return the queues it got."""
    captured: dict[str, Any] = {}

    class FakeConsumer:
        async def consume(self) -> None:
            return None

    def fake_create_consumer(**kwargs: Any) -> FakeConsumer:
        captured.update(kwargs)
        return FakeConsumer()

    worker = MessageBusWorker(
        app=Microservice(providers=[], controllers=CONTROLLERS, interceptors=[]),
        broker_url="amqp://guest:guest@localhost:5672/?exchange=main&prefetch_count=1",
        backend_url="redis://localhost:6379",
        handler_names=handler_names,
        handler_groups=handler_groups,
    )
    with patch(
        "jararaca.messagebus.worker.get_message_broker_backend_from_url",
        return_value=MagicMock(),
    ), patch(
        "jararaca.messagebus.worker.create_message_bus_consumer",
        side_effect=fake_create_consumer,
    ):
        await worker.start_async()

    return queues(captured["message_handler_set"], captured["scheduled_actions"])


@pytest.fixture(autouse=True)
def reset_instantiated() -> None:
    instantiated.clear()


async def test_group_filter_skips_controllers_of_other_groups() -> None:
    consumed = await run_worker(handler_groups={"A"})

    assert instantiated == ["A"]
    assert consumed == {
        f"orders.placed.{__name__}.GroupAController.on_order",
        f"{__name__}.GroupAController.sweep",
    }


async def test_name_filter_skips_controllers_without_a_matching_name() -> None:
    consumed = await run_worker(handler_names={"invoices"})

    assert instantiated == ["B"]
    assert consumed == {f"invoices.issued.{__name__}.GroupBController.on_invoice"}


async def test_no_filter_instantiates_every_messagebus_controller() -> None:
    consumed = await run_worker()

    assert instantiated == ["A", "B"]
    assert consumed == queues_selected_after_instantiating_everything(None, None)


@pytest.mark.parametrize(
    ("handler_names", "handler_groups"),
    [
        (None, {"A"}),
        (None, {"B"}),
        (None, {"A", "B"}),
        (None, {"missing"}),
        ({"orders"}, None),
        ({"invoices-in-a", "invoices"}, None),
        ({"orders", "invoices"}, {"B"}),
        ({"sweep"}, {"A"}),
    ],
)
async def test_consumes_the_same_queues_as_before(
    handler_names: set[str] | None, handler_groups: set[str] | None
) -> None:
    expected = queues_selected_after_instantiating_everything(
        handler_names, handler_groups
    )
    instantiated.clear()

    assert await run_worker(handler_names, handler_groups) == expected
