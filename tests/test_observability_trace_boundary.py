# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the tracing boundary policy between the HTTP layer and the message bus.
"""

from datetime import datetime
from typing import Any, Generator, cast
from unittest.mock import patch

import pytest
from opentelemetry import baggage
from opentelemetry import context as context_api
from opentelemetry import trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from jararaca.messagebus.implicit_headers import (
    ImplicitHeaders,
    provide_implicit_headers,
    use_implicit_headers,
)
from jararaca.messagebus.message import Message, MessageOf
from jararaca.microservice import (
    AppTransactionContext,
    MessageBusTransactionData,
    SchedulerTransactionData,
)
from jararaca.observability.providers import otel
from jararaca.observability.providers.otel import (
    FLOW_ID_ATTRIBUTE,
    FLOW_PARENT_SPAN_ID_ATTRIBUTE,
    FLOW_PARENT_TRACE_ID_ATTRIBUTE,
    OtelTracingContextProviderFactory,
    build_message_bus_span_name,
    resolve_trace_boundary,
    use_flow_id,
)
from jararaca.reflect.controller_inspect import ControllerMemberReflect


class SampleTaskMessage(Message):
    MESSAGE_TOPIC = "sample.task"


class ChildBoundMessage(Message):
    MESSAGE_TOPIC = "sample.child-bound"
    TRACE_BOUNDARY = "child"


class LinkBoundMessage(Message):
    MESSAGE_TOPIC = "sample.link-bound"
    TRACE_BOUNDARY = "link"


@pytest.fixture
def exporter() -> Generator[InMemorySpanExporter, None, None]:
    span_exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    with patch.object(otel, "tracer", provider.get_tracer(__name__)):
        yield span_exporter


def build_tx_data(
    message_type: type[Message] = SampleTaskMessage,
    processing_attempt: int = 1,
) -> MessageBusTransactionData:
    return MessageBusTransactionData(
        topic=f"{message_type.MESSAGE_TOPIC}.tests.DummyController.handle",
        message=cast(MessageOf[Message], None),
        message_type=message_type,
        message_id="message-id",
        processing_attempt=processing_attempt,
    )


def build_context(
    message_type: type[Message] = SampleTaskMessage,
    processing_attempt: int = 1,
) -> AppTransactionContext:
    """Builds an app transaction context with just what the tracing root setup reads."""

    class DummyController:
        async def handle(self) -> None: ...

    class DummyControllerReflect:
        controller_class = DummyController

    class DummyMemberReflect:
        controller_reflect = DummyControllerReflect()
        member_function = DummyController.handle

    return AppTransactionContext(
        transaction_data=build_tx_data(message_type, processing_attempt),
        controller_member_reflect=cast(ControllerMemberReflect, DummyMemberReflect()),
    )


async def run_message_bus_root(
    headers: ImplicitHeaders,
    message_type: type[Message] = SampleTaskMessage,
    processing_attempt: int = 1,
) -> ImplicitHeaders:
    """Runs the message bus root setup and returns the headers it propagates onwards."""
    factory = OtelTracingContextProviderFactory()
    captured: ImplicitHeaders = {}

    with provide_implicit_headers(headers, reset=True):
        async with factory.root_setup(build_context(message_type, processing_attempt)):
            captured = dict(use_implicit_headers())
            assert use_flow_id() is not None

    return captured


def make_producer_headers() -> ImplicitHeaders:
    """Emits a producer span and returns the headers a publisher would carry."""
    with otel.tracer.start_as_current_span("HTTP POST /webhook"):
        span_context = trace.get_current_span().get_span_context()
        outgoing = baggage.set_baggage(
            otel.FLOW_ID_BAGGAGE_KEY,
            trace.format_trace_id(span_context.trace_id),
            context=context_api.get_current(),
        )
        headers: ImplicitHeaders = {}
        TraceContextTextMapPropagator().inject(headers, context=outgoing)
        W3CBaggagePropagator().inject(headers, context=outgoing)

    return headers


def find_span(exporter: InMemorySpanExporter, name_fragment: str) -> ReadableSpan:
    spans = [
        s for s in exporter.get_finished_spans() if name_fragment in (s.name or "")
    ]
    assert len(spans) == 1, f"expected one span matching {name_fragment!r}, got {spans}"
    return spans[0]


class TestResolveTraceBoundary:

    def test_defaults_to_child_on_first_attempt(self) -> None:
        with patch.object(otel, "OBSERVABILITY_TRACE_ASYNC_BOUNDARY", "child"):
            assert resolve_trace_boundary(build_tx_data()) == "child"

    def test_env_default_can_switch_to_link(self) -> None:
        with patch.object(otel, "OBSERVABILITY_TRACE_ASYNC_BOUNDARY", "link"):
            assert resolve_trace_boundary(build_tx_data()) == "link"

    def test_retry_uses_the_retry_policy(self) -> None:
        with patch.object(otel, "OBSERVABILITY_TRACE_ASYNC_BOUNDARY", "child"):
            assert resolve_trace_boundary(build_tx_data(processing_attempt=2)) == "link"

    def test_explicit_message_policy_wins_over_retry_rule(self) -> None:
        with patch.object(otel, "OBSERVABILITY_TRACE_ASYNC_BOUNDARY", "link"):
            tx_data = build_tx_data(ChildBoundMessage, processing_attempt=3)
            assert resolve_trace_boundary(tx_data) == "child"

    def test_explicit_message_policy_wins_over_env_default(self) -> None:
        with patch.object(otel, "OBSERVABILITY_TRACE_ASYNC_BOUNDARY", "child"):
            assert resolve_trace_boundary(build_tx_data(LinkBoundMessage)) == "link"

    def test_non_message_bus_context_is_always_child(self) -> None:
        tx_data = SchedulerTransactionData(
            task_name="task",
            triggered_at=datetime.now(),
            scheduled_to=datetime.now(),
            cron_expression="* * * * *",
        )
        with patch.object(otel, "OBSERVABILITY_TRACE_ASYNC_BOUNDARY", "link"):
            assert resolve_trace_boundary(tx_data) == "child"


class TestSpanNameStyle:

    def test_legacy_style_keeps_attempt_and_broker_topic(self) -> None:
        with patch.object(
            otel, "OBSERVABILITY_TRACE_MESSAGEBUS_SPAN_NAME_STYLE", "legacy"
        ):
            assert build_message_bus_span_name(build_tx_data(processing_attempt=2)) == (
                "Att#2 Message Bus sample.task.tests.DummyController.handle"
            )

    def test_compact_style_uses_message_type_and_topic(self) -> None:
        with patch.object(
            otel, "OBSERVABILITY_TRACE_MESSAGEBUS_SPAN_NAME_STYLE", "compact"
        ):
            tx_data = build_tx_data(processing_attempt=2)
            assert build_message_bus_span_name(tx_data) == "TASK sample.task"


class TestRootSetupBoundary:

    async def test_child_mode_keeps_a_single_trace(
        self, exporter: InMemorySpanExporter
    ) -> None:
        headers = make_producer_headers()

        with patch.object(otel, "OBSERVABILITY_TRACE_ASYNC_BOUNDARY", "child"):
            await run_message_bus_root(headers)

        producer = find_span(exporter, "HTTP POST")
        consumer = find_span(exporter, "Message Bus")

        assert consumer.context is not None and producer.context is not None
        assert consumer.context.trace_id == producer.context.trace_id
        assert consumer.parent is not None
        assert consumer.parent.span_id == producer.context.span_id
        assert not consumer.links

    async def test_link_mode_starts_a_new_trace_linked_to_the_producer(
        self, exporter: InMemorySpanExporter
    ) -> None:
        headers = make_producer_headers()

        with patch.object(otel, "OBSERVABILITY_TRACE_ASYNC_BOUNDARY", "link"):
            await run_message_bus_root(headers)

        producer = find_span(exporter, "HTTP POST")
        consumer = find_span(exporter, "Message Bus")

        assert consumer.context is not None and producer.context is not None
        assert consumer.context.trace_id != producer.context.trace_id
        assert consumer.parent is None, "should be a trace root"

        assert len(consumer.links) == 1
        link = consumer.links[0]
        assert link.context.trace_id == producer.context.trace_id
        assert link.context.span_id == producer.context.span_id

        attributes: dict[str, Any] = dict(consumer.attributes or {})
        assert attributes[FLOW_PARENT_TRACE_ID_ATTRIBUTE] == trace.format_trace_id(
            producer.context.trace_id
        )
        assert attributes[FLOW_PARENT_SPAN_ID_ATTRIBUTE] == trace.format_span_id(
            producer.context.span_id
        )

    async def test_flow_id_survives_the_boundary(
        self, exporter: InMemorySpanExporter
    ) -> None:
        headers = make_producer_headers()

        with patch.object(otel, "OBSERVABILITY_TRACE_ASYNC_BOUNDARY", "link"):
            forwarded = await run_message_bus_root(headers)

        producer = find_span(exporter, "HTTP POST")
        consumer = find_span(exporter, "Message Bus")
        assert producer.context is not None

        expected_flow_id = trace.format_trace_id(producer.context.trace_id)
        attributes: dict[str, Any] = dict(consumer.attributes or {})

        # Both traces of the arc carry the same flow id...
        assert attributes[FLOW_ID_ATTRIBUTE] == expected_flow_id
        # ...and it is forwarded to whatever this handler publishes next.
        assert expected_flow_id in str(forwarded.get("baggage", ""))

    async def test_flow_id_defaults_to_own_trace_id_without_incoming_baggage(
        self, exporter: InMemorySpanExporter
    ) -> None:
        with patch.object(otel, "OBSERVABILITY_TRACE_ASYNC_BOUNDARY", "link"):
            await run_message_bus_root({})

        consumer = find_span(exporter, "Message Bus")
        assert consumer.context is not None

        attributes: dict[str, Any] = dict(consumer.attributes or {})
        assert attributes[FLOW_ID_ATTRIBUTE] == trace.format_trace_id(
            consumer.context.trace_id
        )
        assert not consumer.links
