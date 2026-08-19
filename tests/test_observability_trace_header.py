# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the trace header echoed back on error responses.
"""

import re
from typing import Any, Generator

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from jararaca.observability.constants import TRACEPARENT_KEY
from jararaca.observability.fastapi_exception_handler import (
    setup_fastapi_exception_handler,
)
from jararaca.observability.providers.otel import format_traceparent

W3C_TRACEPARENT = re.compile(
    r"^00-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-[0-9a-f]{2}$"
)


@pytest.fixture
def tracer() -> Generator[trace.Tracer, None, None]:
    provider = TracerProvider()
    yield provider.get_tracer(__name__)


def build_app(tracer: trace.Tracer, *, publish_scope: bool = True) -> FastAPI:
    """An app whose root span mimics what the observability interceptor sets up."""
    app = FastAPI()

    @app.middleware("http")
    async def root_span(request: Request, call_next: Any) -> Any:
        with tracer.start_as_current_span("root") as span:
            if publish_scope:
                request.scope[TRACEPARENT_KEY] = format_traceparent(
                    span.get_span_context()
                )
            return await call_next(request)

    @app.get("/unhandled")
    def unhandled() -> None:
        raise RuntimeError("kaboom")

    @app.get("/teapot")
    def teapot() -> None:
        raise HTTPException(status_code=418)

    @app.get("/validated")
    def validated(number: int) -> int:
        return number

    return app


@pytest.mark.parametrize(
    "path, expected_status",
    [
        ("/unhandled", 500),
        ("/teapot", 418),
        ("/validated", 422),
    ],
)
def test_error_responses_carry_a_valid_traceparent(
    tracer: trace.Tracer, path: str, expected_status: int
) -> None:
    app = build_app(tracer)
    setup_fastapi_exception_handler(app)

    response = TestClient(app, raise_server_exceptions=False).get(path)

    assert response.status_code == expected_status
    assert W3C_TRACEPARENT.match(response.headers["traceparent"]) is not None


def test_unhandled_error_keeps_the_default_starlette_body(
    tracer: trace.Tracer,
) -> None:
    app = build_app(tracer)
    setup_fastapi_exception_handler(app)

    response = TestClient(app, raise_server_exceptions=False).get("/unhandled")

    assert response.status_code == 500
    assert response.text == "Internal Server Error"


def test_traceparent_matches_the_trace_id_published_in_the_scope(
    tracer: trace.Tracer,
) -> None:
    app = build_app(tracer)
    setup_fastapi_exception_handler(app)
    seen: dict[str, str] = {}

    @app.middleware("http")
    async def capture(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        seen["scope"] = request.scope.get(TRACEPARENT_KEY, "")
        return response

    response = TestClient(app, raise_server_exceptions=False).get("/teapot")

    assert seen["scope"] == response.headers["traceparent"]


def test_header_is_omitted_when_there_is_no_trace() -> None:
    app = FastAPI()

    @app.get("/unhandled")
    def unhandled() -> None:
        raise RuntimeError("kaboom")

    setup_fastapi_exception_handler(app)

    response = TestClient(app, raise_server_exceptions=False).get("/unhandled")

    assert response.status_code == 500
    # An empty `traceparent` is not a valid header value, so none is better than blank.
    assert "traceparent" not in response.headers


def test_custom_header_name_is_honoured(tracer: trace.Tracer) -> None:
    app = build_app(tracer)
    setup_fastapi_exception_handler(app, trace_header_name="x-trace-id")

    response = TestClient(app, raise_server_exceptions=False).get("/unhandled")

    assert W3C_TRACEPARENT.match(response.headers["x-trace-id"]) is not None
    assert "traceparent" not in response.headers


@pytest.mark.parametrize("registration_key", [Exception, 500])
def test_existing_server_error_handler_is_wrapped_not_replaced(
    tracer: trace.Tracer, registration_key: Any
) -> None:
    app = build_app(tracer)

    async def custom(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "custom"}, status_code=503)

    app.exception_handlers[registration_key] = custom
    setup_fastapi_exception_handler(app)

    response = TestClient(app, raise_server_exceptions=False).get("/unhandled")

    assert response.status_code == 503
    assert response.json() == {"detail": "custom"}
    assert W3C_TRACEPARENT.match(response.headers["traceparent"]) is not None


def test_sync_server_error_handler_is_supported(tracer: trace.Tracer) -> None:
    app = build_app(tracer)

    def custom(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "sync"}, status_code=502)

    app.exception_handlers[Exception] = custom
    setup_fastapi_exception_handler(app)

    response = TestClient(app, raise_server_exceptions=False).get("/unhandled")

    assert response.status_code == 502
    assert response.json() == {"detail": "sync"}
    assert W3C_TRACEPARENT.match(response.headers["traceparent"]) is not None


def test_format_traceparent_returns_empty_for_an_invalid_span_context() -> None:
    assert format_traceparent(trace.INVALID_SPAN_CONTEXT) == ""
