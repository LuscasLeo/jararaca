# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for echoing the trace context back on successful responses.
"""

import re
from typing import Any, Generator

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from jararaca.observability.constants import TRACEPARENT_KEY
from jararaca.observability.fastapi_exception_handler import (
    setup_fastapi_exception_handler,
)
from jararaca.observability.trace_context import format_traceparent
from jararaca.observability.trace_response import TraceResponseHeaderMiddleware

W3C_TRACEPARENT = re.compile(
    r"^00-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-[0-9a-f]{2}$"
)


@pytest.fixture
def tracer() -> Generator[trace.Tracer, None, None]:
    yield TracerProvider().get_tracer(__name__)


def build_app(
    tracer: trace.Tracer, *, publish_scope: bool = True, **options: Any
) -> FastAPI:
    app = FastAPI()

    @app.get("/ok")
    def ok() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/teapot")
    def teapot() -> None:
        raise HTTPException(status_code=418)

    @app.get("/unhandled")
    def unhandled() -> None:
        raise RuntimeError("kaboom")

    app.add_middleware(TraceResponseHeaderMiddleware, **options)

    if publish_scope:

        @app.middleware("http")
        async def root_span(request: Request, call_next: Any) -> Any:
            # Stands in for what the observability interceptor publishes.
            with tracer.start_as_current_span("root") as span:
                request.scope[TRACEPARENT_KEY] = format_traceparent(
                    span.get_span_context()
                )
                return await call_next(request)

    return app


def test_successful_responses_carry_the_trace_header(tracer: trace.Tracer) -> None:
    response = TestClient(build_app(tracer)).get("/ok")

    assert response.status_code == 200
    assert W3C_TRACEPARENT.match(response.headers["traceparent"]) is not None


def test_the_header_matches_what_the_scope_published(tracer: trace.Tracer) -> None:
    app = build_app(tracer)
    seen: dict[str, str] = {}

    @app.middleware("http")
    async def capture(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        seen["scope"] = request.scope.get(TRACEPARENT_KEY, "")
        return response

    response = TestClient(app).get("/ok")

    assert seen["scope"] == response.headers["traceparent"]


@pytest.mark.parametrize("path, status", [("/teapot", 418), ("/unhandled", 500)])
def test_error_responses_still_carry_it(
    tracer: trace.Tracer, path: str, status: int
) -> None:
    app = build_app(tracer)
    setup_fastapi_exception_handler(app)

    response = TestClient(app, raise_server_exceptions=False).get(path)

    assert response.status_code == status
    assert W3C_TRACEPARENT.match(response.headers["traceparent"]) is not None


def test_no_duplicate_header_when_combined_with_the_exception_handler(
    tracer: trace.Tracer,
) -> None:
    app = build_app(tracer)
    setup_fastapi_exception_handler(app)

    response = TestClient(app, raise_server_exceptions=False).get("/teapot")

    assert response.headers.get_list("traceparent") == [response.headers["traceparent"]]


def test_a_custom_header_name_is_used(tracer: trace.Tracer) -> None:
    response = TestClient(build_app(tracer, header_name="x-trace-id")).get("/ok")

    assert W3C_TRACEPARENT.match(response.headers["x-trace-id"]) is not None
    assert "traceparent" not in response.headers


def test_falls_back_to_the_entry_span_without_a_scope_value(
    tracer: trace.Tracer,
) -> None:
    app = build_app(tracer, publish_scope=False)

    # No interceptor published anything, so only a live span can supply the value.
    with tracer.start_as_current_span("outer"):
        response = TestClient(app).get("/ok")

    assert W3C_TRACEPARENT.match(response.headers["traceparent"]) is not None


def test_header_is_omitted_when_there_is_no_trace() -> None:
    app = FastAPI()

    @app.get("/ok")
    def ok() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(TraceResponseHeaderMiddleware)

    response = TestClient(app).get("/ok")

    assert response.status_code == 200
    assert "traceparent" not in response.headers


class TestCorsExposure:
    """
    Exposure only works when this middleware sits *outside* `CORSMiddleware`, so it sees
    the CORS headers on the way out. Starlette's `add_middleware` puts the last added
    one outermost, so it has to be added after.
    """

    @staticmethod
    def cors_app(
        tracer: trace.Tracer, *, outside_cors: bool, **options: Any
    ) -> FastAPI:
        app = FastAPI()

        @app.get("/ok")
        def ok() -> dict[str, bool]:
            return {"ok": True}

        @app.middleware("http")
        async def root_span(request: Request, call_next: Any) -> Any:
            with tracer.start_as_current_span("root") as span:
                request.scope[TRACEPARENT_KEY] = format_traceparent(
                    span.get_span_context()
                )
                return await call_next(request)

        if outside_cors:
            app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])
            app.add_middleware(TraceResponseHeaderMiddleware, **options)
        else:
            app.add_middleware(TraceResponseHeaderMiddleware, **options)
            app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])

        return app

    def request(self, app: FastAPI) -> Any:
        return TestClient(app).get(
            "/ok", headers={"origin": "https://beta.datacred.net"}
        )

    def test_header_is_exposed_to_cross_origin_callers(
        self, tracer: trace.Tracer
    ) -> None:
        response = self.request(self.cors_app(tracer, outside_cors=True))

        exposed = response.headers["access-control-expose-headers"]

        assert "traceparent" in [part.strip() for part in exposed.split(",")]
        assert W3C_TRACEPARENT.match(response.headers["traceparent"]) is not None

    def test_exposure_needs_this_middleware_outside_cors(
        self, tracer: trace.Tracer
    ) -> None:
        # Added before CORSMiddleware it is the inner one, so it sends before the CORS
        # headers exist. The trace header is still set, only the exposure is lost.
        response = self.request(self.cors_app(tracer, outside_cors=False))

        assert W3C_TRACEPARENT.match(response.headers["traceparent"]) is not None
        assert "access-control-expose-headers" not in response.headers

    def test_exposure_can_be_turned_off(self, tracer: trace.Tracer) -> None:
        response = self.request(
            self.cors_app(tracer, outside_cors=True, expose_via_cors=False)
        )

        assert "access-control-expose-headers" not in response.headers

    def test_nothing_is_added_to_a_response_without_cors(
        self, tracer: trace.Tracer
    ) -> None:
        response = TestClient(build_app(tracer)).get("/ok")

        assert "access-control-expose-headers" not in response.headers
