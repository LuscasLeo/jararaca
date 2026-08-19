# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for enriching the root span of a transaction from arbitrarily deep code.
"""

from datetime import datetime
from typing import Any, Generator, cast

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from jararaca.microservice import AppTransactionContext, SchedulerTransactionData
from jararaca.observability.decorators import provide_tracing_ctx_provider
from jararaca.observability.hooks import (
    get_root_span,
    set_root_span_attribute,
    set_root_span_attributes,
    set_span_attribute,
    start_span,
)
from jararaca.observability.providers import otel
from jararaca.observability.providers.otel import (
    OtelTracingContextProviderFactory,
    OtelTracingSpan,
)
from jararaca.reflect.controller_inspect import ControllerMemberReflect

ROOT_SPAN_NAME = "Scheduler Task nightly"


@pytest.fixture
def exporter() -> Generator[InMemorySpanExporter, None, None]:
    span_exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    original = otel.tracer
    otel.tracer = provider.get_tracer(__name__)
    try:
        yield span_exporter
    finally:
        otel.tracer = original


def build_app_context() -> AppTransactionContext:
    triggered_at = datetime(2026, 8, 19, 10, 36, 2)
    tx_data = SchedulerTransactionData(
        task_name="nightly",
        scheduled_to=triggered_at,
        cron_expression="0 0 * * *",
        triggered_at=triggered_at,
        slot_wait_seconds=0.0,
    )

    class DummyController:
        def handle(self) -> None: ...

    class DummyControllerReflect:
        controller_class = DummyController

    class DummyMemberReflect:
        controller_reflect = DummyControllerReflect()
        member_function = DummyController.handle

    return AppTransactionContext(
        transaction_data=tx_data,
        controller_member_reflect=cast(ControllerMemberReflect, DummyMemberReflect()),
    )


async def run_transaction(body: Any) -> None:
    """Drive a transaction the way ObservabilityInterceptor does."""
    factory = OtelTracingContextProviderFactory()
    app_context = build_app_context()

    async with factory.root_setup(app_context):
        with provide_tracing_ctx_provider(factory.provide_provider(app_context)):
            body()


def find_span(exporter: InMemorySpanExporter, name: str) -> ReadableSpan:
    return next(span for span in exporter.get_finished_spans() if span.name == name)


async def test_attribute_set_from_a_deeply_nested_span_lands_on_the_root(
    exporter: InMemorySpanExporter,
) -> None:
    def body() -> None:
        with start_span("outer"):
            with start_span("middle"):
                with start_span("inner"):
                    set_root_span_attribute("simulation.id", "abc-123")

    await run_transaction(body)

    root = find_span(exporter, ROOT_SPAN_NAME)
    assert (root.attributes or {})["simulation.id"] == "abc-123"


async def test_the_nested_span_itself_is_untouched(
    exporter: InMemorySpanExporter,
) -> None:
    def body() -> None:
        with start_span("inner"):
            set_root_span_attribute("on.root", "yes")
            set_span_attribute("on.inner", "yes")

    await run_transaction(body)

    root = find_span(exporter, ROOT_SPAN_NAME)
    inner = find_span(exporter, "inner")

    assert "on.root" in (root.attributes or {})
    assert "on.root" not in (inner.attributes or {})
    assert "on.inner" in (inner.attributes or {})
    assert "on.inner" not in (root.attributes or {})


async def test_bulk_attributes(exporter: InMemorySpanExporter) -> None:
    def body() -> None:
        with start_span("inner"):
            set_root_span_attributes({"tenant": "datacred", "records": 42})

    await run_transaction(body)

    attributes = find_span(exporter, ROOT_SPAN_NAME).attributes or {}
    assert attributes["tenant"] == "datacred"
    assert attributes["records"] == 42


async def test_get_root_span_returns_the_root_not_the_current_span(
    exporter: InMemorySpanExporter,
) -> None:
    captured: dict[str, Any] = {}

    def body() -> None:
        with start_span("inner"):
            root = get_root_span()
            assert isinstance(root, OtelTracingSpan)
            captured["name"] = root.span.name  # type: ignore[attr-defined]

    await run_transaction(body)

    assert captured["name"] == ROOT_SPAN_NAME


async def test_attributes_survive_after_the_nested_span_closed(
    exporter: InMemorySpanExporter,
) -> None:
    def body() -> None:
        with start_span("inner"):
            pass
        # Still inside the transaction, but no nested span is current any more.
        set_root_span_attribute("late", "value")

    await run_transaction(body)

    assert (find_span(exporter, ROOT_SPAN_NAME).attributes or {})["late"] == "value"


def test_hooks_are_inert_without_a_transaction() -> None:
    # No tracing context provider is installed outside a transaction.
    set_root_span_attribute("ignored", "value")
    set_root_span_attributes({"ignored": "value"})

    assert get_root_span() is None
