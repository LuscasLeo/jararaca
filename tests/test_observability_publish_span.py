# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the PRODUCER span recorded when a message is handed to the broker.
"""

from typing import Any, Generator, cast
from unittest.mock import patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from jararaca.messagebus.implicit_headers import (
    ImplicitHeaders,
    provide_implicit_headers,
)
from jararaca.messagebus.message import Message, MessageOf
from jararaca.microservice import AppTransactionContext, MessageBusTransactionData
from jararaca.observability.hooks import start_message_publish_span
from jararaca.observability.providers import otel
from jararaca.observability.providers.otel import (
    FLOW_ID_ATTRIBUTE,
    OtelTracingContextProviderFactory,
)
from jararaca.reflect.controller_inspect import ControllerMemberReflect

TOPIC = "sample.task"


class SampleTaskMessage(Message):
    MESSAGE_TOPIC = TOPIC


@pytest.fixture
def exporter() -> Generator[InMemorySpanExporter, None, None]:
    span_exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    with patch.object(otel, "tracer", provider.get_tracer(__name__)):
        yield span_exporter


def build_context() -> AppTransactionContext:
    class DummyController:
        async def handle(self) -> None: ...

    class DummyControllerReflect:
        controller_class = DummyController

    class DummyMemberReflect:
        controller_reflect = DummyControllerReflect()
        member_function = DummyController.handle

    return AppTransactionContext(
        transaction_data=MessageBusTransactionData(
            topic=f"{TOPIC}.tests.DummyController.handle",
            message=cast(MessageOf[Message], None),
            message_type=SampleTaskMessage,
            message_id="message-id",
            processing_attempt=1,
        ),
        controller_member_reflect=cast(ControllerMemberReflect, DummyMemberReflect()),
    )


def publish_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [
        s for s in exporter.get_finished_spans() if (s.name or "").startswith("PUBLISH")
    ]


def transaction_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [
        s
        for s in exporter.get_finished_spans()
        if dict(s.attributes or {}).get("app.context_type") == "message_bus"
    ]


def attributes_of(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


async def publish_within_transaction(**kwargs: Any) -> ImplicitHeaders:
    """Runs a publish inside a transaction root span, as the interceptor would."""
    captured: ImplicitHeaders = {}

    async with OtelTracingContextProviderFactory().root_setup(build_context()):
        with start_message_publish_span(topic=TOPIC, **kwargs) as headers:
            captured = dict(headers)

    return captured


def extract_span_context(headers: ImplicitHeaders) -> trace.SpanContext:
    carrier = {str(k): str(v) for k, v in headers.items() if v is not None}
    ctx = TraceContextTextMapPropagator().extract(carrier)
    return trace.get_current_span(ctx).get_span_context()


class TestPublishSpan:

    async def test_publish_records_a_producer_span(
        self, exporter: InMemorySpanExporter
    ) -> None:
        await publish_within_transaction()

        spans = publish_spans(exporter)
        assert len(spans) == 1
        assert spans[0].name == f"PUBLISH {TOPIC}"
        assert spans[0].kind == trace.SpanKind.PRODUCER

    async def test_publish_span_is_a_child_of_the_producing_transaction(
        self, exporter: InMemorySpanExporter
    ) -> None:
        await publish_within_transaction()

        span = publish_spans(exporter)[0]
        root = transaction_spans(exporter)[0]
        assert root.context is not None and span.context is not None

        assert span.parent is not None
        assert span.parent.span_id == root.context.span_id
        assert span.context.trace_id == root.context.trace_id

    async def test_headers_carry_the_publish_span_not_the_root_span(
        self, exporter: InMemorySpanExporter
    ) -> None:
        headers = await publish_within_transaction()

        span = publish_spans(exporter)[0]
        root = transaction_spans(exporter)[0]
        assert span.context is not None and root.context is not None

        propagated = extract_span_context(headers)
        assert propagated.span_id == span.context.span_id
        assert propagated.span_id != root.context.span_id

    async def test_publish_span_carries_message_attributes(
        self, exporter: InMemorySpanExporter
    ) -> None:
        await publish_within_transaction(
            destination="main-exchange",
            routing_key=f"{TOPIC}.#",
            message_name="SampleTaskMessage",
            message_module="tests.messages",
            message_type="task",
            message_category="uncategorized",
        )

        attributes = attributes_of(publish_spans(exporter)[0])
        assert attributes["bus.message.topic"] == TOPIC
        assert attributes["bus.message.routing_key"] == f"{TOPIC}.#"
        assert attributes["bus.message.name"] == "SampleTaskMessage"
        assert attributes["bus.message.type"] == "task"
        assert attributes["messaging.destination.name"] == "main-exchange"
        assert attributes["messaging.operation"] == "publish"
        assert attributes["bus.publish.mode"] == "immediate"

    async def test_publish_span_shares_the_flow_id_of_the_transaction(
        self, exporter: InMemorySpanExporter
    ) -> None:
        await publish_within_transaction()

        published = attributes_of(publish_spans(exporter)[0])
        root = attributes_of(transaction_spans(exporter)[0])
        assert published[FLOW_ID_ATTRIBUTE] == root[FLOW_ID_ATTRIBUTE]

    async def test_delayed_mode_records_the_dispatch_time(
        self, exporter: InMemorySpanExporter
    ) -> None:
        await publish_within_transaction(mode="delayed", dispatch_time=1786715261)

        attributes = attributes_of(publish_spans(exporter)[0])
        assert attributes["bus.publish.mode"] == "delayed"
        assert attributes["bus.publish.dispatch_time"] == 1786715261

    async def test_application_implicit_headers_are_preserved(
        self, exporter: InMemorySpanExporter
    ) -> None:
        with provide_implicit_headers({"x-tenant": "acme"}):
            headers = await publish_within_transaction()

        assert headers["x-tenant"] == "acme"
        assert "traceparent" in headers

    async def test_baggage_flows_through_the_publish_span(
        self, exporter: InMemorySpanExporter
    ) -> None:
        headers = await publish_within_transaction()

        flow_id = attributes_of(transaction_spans(exporter)[0])[FLOW_ID_ATTRIBUTE]
        assert str(flow_id) in str(headers["baggage"])

    async def test_can_be_disabled(self, exporter: InMemorySpanExporter) -> None:
        with patch.object(otel, "OBSERVABILITY_TRACE_PUBLISH_SPAN", False):
            headers = await publish_within_transaction()

        assert not publish_spans(exporter)

        # Falls back to the previous behaviour: the transaction root span context.
        root = transaction_spans(exporter)[0]
        assert root.context is not None
        assert extract_span_context(headers).span_id == root.context.span_id


class TestPublishSpanLinksTheConsumer:

    async def test_consumer_links_to_the_publish_span(
        self, exporter: InMemorySpanExporter
    ) -> None:
        with patch.object(otel, "OBSERVABILITY_TRACE_ASYNC_BOUNDARY", "link"):
            headers = await publish_within_transaction()

            with provide_implicit_headers(headers, reset=True):
                async with OtelTracingContextProviderFactory().root_setup(
                    build_context()
                ):
                    pass

        published = publish_spans(exporter)[0]
        assert published.context is not None

        # The second transaction root span is the consumer side.
        consumer = transaction_spans(exporter)[-1]
        assert consumer.context is not None

        assert len(consumer.links) == 1
        assert consumer.links[0].context.span_id == published.context.span_id, (
            "the consumer must link to the publish moment, not to the producing "
            "transaction root span"
        )
        assert consumer.context.trace_id != published.context.trace_id

    async def test_child_mode_nests_the_consumer_under_the_publish_span(
        self, exporter: InMemorySpanExporter
    ) -> None:
        with patch.object(otel, "OBSERVABILITY_TRACE_ASYNC_BOUNDARY", "child"):
            headers = await publish_within_transaction()

            with provide_implicit_headers(headers, reset=True):
                async with OtelTracingContextProviderFactory().root_setup(
                    build_context()
                ):
                    pass

        published = publish_spans(exporter)[0]
        consumer = transaction_spans(exporter)[-1]
        assert published.context is not None

        assert consumer.parent is not None
        assert consumer.parent.span_id == published.context.span_id
