# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for recording the HTTP response as a span event.
"""

import json
from typing import Any, Generator

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from starlette.types import Receive, Scope, Send

from jararaca.observability.http_response_event import (
    DEFAULT_EVENT_NAME,
    REDACTED_HEADER_VALUE,
    HttpResponseEventMiddleware,
)

SERVER_SPAN_NAME = "server"


@pytest.fixture
def exporter() -> Generator[InMemorySpanExporter, None, None]:
    span_exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    yield span_exporter


class FakeServerSpanMiddleware:
    """Stands in for the ASGI instrumentation's long lived SERVER span."""

    def __init__(self, app: Any, tracer: trace.Tracer) -> None:
        self.app = app
        self.tracer = tracer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        with self.tracer.start_as_current_span(SERVER_SPAN_NAME):
            await self.app(scope, receive, send)


def build_app(exporter: InMemorySpanExporter, **options: Any) -> FastAPI:
    app = FastAPI()

    @app.get("/json")
    def json_endpoint() -> dict[str, str]:
        return {"token": "s3cret", "name": "Natan"}

    @app.get("/text")
    def text_endpoint() -> PlainTextResponse:
        return PlainTextResponse("plain body")

    @app.get("/binary")
    def binary_endpoint() -> PlainTextResponse:
        return PlainTextResponse(b"\x00\x01\x02", media_type="application/octet-stream")

    @app.get("/cookies")
    def cookies_endpoint() -> PlainTextResponse:
        response = PlainTextResponse("ok")
        response.set_cookie("session", "abc")
        response.set_cookie("refresh", "def")
        return response

    @app.get("/stream")
    def stream_endpoint() -> StreamingResponse:
        def chunks() -> Any:
            for index in range(4):
                yield f"chunk-{index};"

        return StreamingResponse(chunks(), media_type="text/plain")

    # Inner: added before the span middleware so it runs inside the server span.
    app.add_middleware(HttpResponseEventMiddleware, **options)

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)
    app.add_middleware(FakeServerSpanMiddleware, tracer=tracer)
    return app


def server_span(exporter: InMemorySpanExporter) -> ReadableSpan:
    return next(
        span for span in exporter.get_finished_spans() if span.name == SERVER_SPAN_NAME
    )


def response_event(exporter: InMemorySpanExporter) -> Any:
    events = [
        event
        for event in server_span(exporter).events
        if event.name == DEFAULT_EVENT_NAME
    ]
    assert len(events) == 1, f"expected exactly one event, got {len(events)}"
    return events[0]


def test_event_is_added_with_status_and_size(
    exporter: InMemorySpanExporter,
) -> None:
    TestClient(build_app(exporter)).get("/json")

    attributes = dict(response_event(exporter).attributes or {})

    assert attributes["http.response.status_code"] == 200
    assert attributes["http.response.body.size"] > 0


def test_body_is_not_captured_by_default(exporter: InMemorySpanExporter) -> None:
    TestClient(build_app(exporter)).get("/json")

    attributes = dict(response_event(exporter).attributes or {})

    # The default has to be safe: sign in responses carry freshly issued tokens.
    assert "http.response.body" not in attributes


def test_body_is_captured_when_asked_for(exporter: InMemorySpanExporter) -> None:
    TestClient(build_app(exporter, capture_body=True)).get("/json")

    attributes = dict(response_event(exporter).attributes or {})

    assert json.loads(str(attributes["http.response.body"]))["name"] == "Natan"


def test_body_is_truncated_to_the_budget(exporter: InMemorySpanExporter) -> None:
    TestClient(build_app(exporter, capture_body=True, max_body_size=5)).get("/json")

    attributes = dict(response_event(exporter).attributes or {})

    assert len(str(attributes["http.response.body"])) == 5
    assert attributes["http.response.body.truncated"] is True


def test_binary_bodies_are_measured_but_not_captured(
    exporter: InMemorySpanExporter,
) -> None:
    TestClient(build_app(exporter, capture_body=True)).get("/binary")

    attributes = dict(response_event(exporter).attributes or {})

    assert attributes["http.response.body.size"] == 3
    assert "http.response.body" not in attributes


def test_streaming_responses_emit_a_single_event_with_the_full_size(
    exporter: InMemorySpanExporter,
) -> None:
    TestClient(build_app(exporter, capture_body=True)).get("/stream")

    attributes = dict(response_event(exporter).attributes or {})

    assert attributes["http.response.body.size"] == len(
        "chunk-0;chunk-1;chunk-2;chunk-3;"
    )
    assert str(attributes["http.response.body"]).startswith("chunk-0;")


class TestResponseHeaders:

    def test_every_header_is_captured_by_default(
        self, exporter: InMemorySpanExporter
    ) -> None:
        TestClient(build_app(exporter)).get("/json")

        attributes = dict(response_event(exporter).attributes or {})

        assert str(attributes["http.response.header.content-type"]).startswith(
            "application/json"
        )
        assert "http.response.header.content-length" in attributes

    def test_sensitive_headers_are_redacted(
        self, exporter: InMemorySpanExporter
    ) -> None:
        TestClient(build_app(exporter)).get("/cookies")

        attributes = dict(response_event(exporter).attributes or {})

        assert attributes["http.response.header.set-cookie"] == (
            REDACTED_HEADER_VALUE,
            REDACTED_HEADER_VALUE,
        )

    def test_repeated_headers_are_recorded_as_a_sequence(
        self, exporter: InMemorySpanExporter
    ) -> None:
        TestClient(build_app(exporter, sensitive_headers=())).get("/cookies")

        attributes = dict(response_event(exporter).attributes or {})
        cookies = attributes["http.response.header.set-cookie"]

        assert isinstance(cookies, tuple) and len(cookies) == 2
        assert any("session=abc" in cookie for cookie in cookies)

    def test_allowlist_narrows_what_is_captured(
        self, exporter: InMemorySpanExporter
    ) -> None:
        TestClient(build_app(exporter, capture_headers=["content-type"])).get("/json")

        attributes = dict(response_event(exporter).attributes or {})

        assert "http.response.header.content-type" in attributes
        assert "http.response.header.content-length" not in attributes

    def test_redaction_wins_over_the_allowlist(
        self, exporter: InMemorySpanExporter
    ) -> None:
        TestClient(build_app(exporter, capture_headers=["set-cookie"])).get("/cookies")

        attributes = dict(response_event(exporter).attributes or {})

        assert attributes["http.response.header.set-cookie"] == (
            REDACTED_HEADER_VALUE,
            REDACTED_HEADER_VALUE,
        )


def test_redactor_can_rewrite_the_body(exporter: InMemorySpanExporter) -> None:
    def redact(scope: Scope, body: bytes) -> bytes:
        payload = json.loads(body)
        payload["token"] = "[redacted]"
        return json.dumps(payload).encode()

    TestClient(build_app(exporter, capture_body=True, redact=redact)).get("/json")

    attributes = dict(response_event(exporter).attributes or {})
    captured = json.loads(str(attributes["http.response.body"]))

    assert captured["token"] == "[redacted]"
    assert captured["name"] == "Natan"


def test_no_event_and_no_crash_without_a_recording_span() -> None:
    app = FastAPI()

    @app.get("/json")
    def json_endpoint() -> dict[str, str]:
        return {"ok": "yes"}

    app.add_middleware(HttpResponseEventMiddleware, capture_body=True)

    response = TestClient(app).get("/json")

    assert response.status_code == 200
    assert response.json() == {"ok": "yes"}
