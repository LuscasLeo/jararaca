# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the inbound trace context trust boundary.
"""

from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.propagate import get_global_textmap, set_global_textmap
from opentelemetry.propagators.textmap import Getter, default_getter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from jararaca.observability.inbound_trace import (
    CLAIMED_HEADER_PREFIX,
    InboundTraceContextMiddleware,
    TrustAwareTextMapPropagator,
    TrustPredicate,
    install_inbound_trace_boundary,
    trust_any_of,
    trust_no_one,
    trust_requests_without_origin,
    trust_when_header_matches,
)

POISONED = "00-01a01a342c0c7f2c96b02810333669f9-04edc31b03b968a8-01"


def build_app(trust: TrustPredicate) -> FastAPI:
    app = FastAPI()

    @app.get("/echo")
    def echo(request: Request) -> dict[str, str | None]:
        return {
            "traceparent": request.headers.get("traceparent"),
            "baggage": request.headers.get("baggage"),
            "claimed": request.headers.get(f"{CLAIMED_HEADER_PREFIX}traceparent"),
        }

    app.add_middleware(InboundTraceContextMiddleware, trust=trust)
    return app


def get(app: FastAPI, **headers: str) -> Any:
    return TestClient(app).get("/echo", headers=headers).json()


def test_untrusted_trace_context_is_not_visible_as_trace_context() -> None:
    body = get(
        build_app(trust_no_one()),
        traceparent=POISONED,
        baggage="flow.id=abc",
    )

    assert body["traceparent"] is None
    assert body["baggage"] is None


def test_untrusted_trace_context_is_preserved_under_a_claimed_header() -> None:
    body = get(build_app(trust_no_one()), traceparent=POISONED)

    assert body["claimed"] == POISONED


def test_unrelated_headers_are_left_alone() -> None:
    app = FastAPI()

    @app.get("/echo")
    def echo(request: Request) -> dict[str, str | None]:
        return {"authorization": request.headers.get("authorization")}

    app.add_middleware(InboundTraceContextMiddleware, trust=trust_no_one())

    response = TestClient(app).get("/echo", headers={"authorization": "Bearer x"})

    assert response.json()["authorization"] == "Bearer x"


class TestTrustRequestsWithoutOrigin:

    def test_browser_traffic_is_distrusted(self) -> None:
        body = get(
            build_app(trust_requests_without_origin()),
            traceparent=POISONED,
            origin="https://beta.datacred.net",
        )

        assert body["traceparent"] is None
        assert body["claimed"] == POISONED

    def test_service_to_service_traffic_keeps_its_trace_context(self) -> None:
        body = get(build_app(trust_requests_without_origin()), traceparent=POISONED)

        assert body["traceparent"] == POISONED

    def test_an_allowed_origin_stays_trusted(self) -> None:
        app = build_app(
            trust_requests_without_origin(allowed_origins=["https://beta.datacred.net"])
        )

        body = get(app, traceparent=POISONED, origin="https://beta.datacred.net")

        assert body["traceparent"] == POISONED

    def test_an_origin_outside_the_allowlist_is_distrusted(self) -> None:
        app = build_app(
            trust_requests_without_origin(allowed_origins=["https://beta.datacred.net"])
        )

        body = get(app, traceparent=POISONED, origin="https://evil.example")

        assert body["traceparent"] is None


class TestTrustWhenHeaderMatches:

    @pytest.fixture
    def app(self) -> FastAPI:
        return build_app(trust_when_header_matches("x-internal-trace", "s3cret"))

    def test_matching_secret_is_trusted(self, app: FastAPI) -> None:
        body = get(app, traceparent=POISONED, **{"x-internal-trace": "s3cret"})

        assert body["traceparent"] == POISONED

    def test_wrong_secret_is_distrusted(self, app: FastAPI) -> None:
        body = get(app, traceparent=POISONED, **{"x-internal-trace": "nope"})

        assert body["traceparent"] is None

    def test_missing_secret_is_distrusted(self, app: FastAPI) -> None:
        body = get(app, traceparent=POISONED)

        assert body["traceparent"] is None


def test_trust_any_of_accepts_either_signal() -> None:
    app = build_app(
        trust_any_of(
            trust_when_header_matches("x-internal-trace", "s3cret"),
            trust_requests_without_origin(),
        )
    )

    via_secret = get(
        app,
        traceparent=POISONED,
        origin="https://beta.datacred.net",
        **{"x-internal-trace": "s3cret"},
    )
    via_absent_origin = get(app, traceparent=POISONED)
    neither = get(app, traceparent=POISONED, origin="https://beta.datacred.net")

    assert via_secret["traceparent"] == POISONED
    assert via_absent_origin["traceparent"] == POISONED
    assert neither["traceparent"] is None


class TestAgainstADownstreamPropagator:
    """
    The point of the middleware is not the header rewrite itself, it is that whatever
    extracts trace context downstream (the ASGI instrumentation, or jararaca's own
    fallback) no longer finds a parent to adopt.
    """

    @staticmethod
    def extracted_parent(trust: TrustPredicate, **headers: str) -> Any:
        from opentelemetry import trace
        from opentelemetry.trace.propagation.tracecontext import (
            TraceContextTextMapPropagator,
        )

        app = FastAPI()
        seen: dict[str, Any] = {}

        @app.get("/echo")
        def echo(request: Request) -> dict[str, str]:
            # Stands in for the extraction done by opentelemetry-instrumentation-asgi.
            context = TraceContextTextMapPropagator().extract(dict(request.headers))
            seen["parent"] = trace.get_current_span(context).get_span_context()
            return {}

        app.add_middleware(InboundTraceContextMiddleware, trust=trust)
        TestClient(app).get("/echo", headers=headers)

        return seen["parent"]

    def test_distrusted_context_leaves_no_parent_to_adopt(self) -> None:
        parent = self.extracted_parent(trust_no_one(), traceparent=POISONED)

        assert parent.is_valid is False

    def test_trusted_context_still_propagates_normally(self) -> None:
        from opentelemetry import trace

        parent = self.extracted_parent(
            trust_requests_without_origin(), traceparent=POISONED
        )

        assert parent.is_valid is True
        assert trace.format_trace_id(parent.trace_id) == POISONED.split("-")[1]
        assert trace.format_span_id(parent.span_id) == POISONED.split("-")[2]


class ScopeGetter(Getter[Any]):
    """Reads headers out of an ASGI scope, like `opentelemetry.instrumentation.asgi`."""

    def get(self, carrier: Any, key: str) -> list[str] | None:
        wanted = key.lower().encode("latin-1")
        values = [
            value.decode("latin-1")
            for name, value in carrier.get("headers") or []
            if name.lower() == wanted
        ]
        return values or None

    def keys(self, carrier: Any) -> list[str]:
        return [name.decode("latin-1") for name, _ in carrier.get("headers") or []]


class TestTrustAwarePropagator:
    """
    The middleware cannot cover `FastAPIInstrumentor`, which patches
    `build_middleware_stack` so `OpenTelemetryMiddleware` wraps the whole stack. The
    propagator is the order independent half of the boundary.
    """

    @staticmethod
    def scope(**headers: str) -> dict[str, Any]:
        return {
            "type": "http",
            "headers": [
                (name.encode("latin-1"), value.encode("latin-1"))
                for name, value in headers.items()
            ],
        }

    @staticmethod
    def extracted(
        trust: TrustPredicate, carrier: Any, getter: Getter[Any] = default_getter
    ) -> Any:
        propagator = TrustAwareTextMapPropagator(trust, TraceContextTextMapPropagator())
        context = propagator.extract(carrier, getter=getter)

        return trace.get_current_span(context).get_span_context()

    def test_distrusted_scope_yields_no_parent(self) -> None:
        parent = self.extracted(
            trust_no_one(), self.scope(traceparent=POISONED), ScopeGetter()
        )

        assert parent.is_valid is False

    def test_trusted_scope_still_extracts(self) -> None:
        parent = self.extracted(
            trust_requests_without_origin(),
            self.scope(traceparent=POISONED),
            ScopeGetter(),
        )

        assert trace.format_trace_id(parent.trace_id) == POISONED.split("-")[1]

    def test_browser_scope_is_distrusted(self) -> None:
        parent = self.extracted(
            trust_requests_without_origin(),
            self.scope(traceparent=POISONED, origin="https://beta.datacred.net"),
            ScopeGetter(),
        )

        assert parent.is_valid is False

    def test_non_asgi_carriers_pass_through_untouched(self) -> None:
        # Message bus headers travel through the same global propagator. Applying the
        # boundary to them would silently break trace continuity across the bus.
        parent = self.extracted(trust_no_one(), {"traceparent": POISONED})

        assert trace.format_trace_id(parent.trace_id) == POISONED.split("-")[1]

    def test_fields_are_delegated(self) -> None:
        propagator = TrustAwareTextMapPropagator(
            trust_no_one(), TraceContextTextMapPropagator()
        )

        assert propagator.fields == TraceContextTextMapPropagator().fields

    def test_installing_twice_replaces_rather_than_stacks(self) -> None:
        original = get_global_textmap()
        try:
            install_inbound_trace_boundary(trust_no_one())
            install_inbound_trace_boundary(trust_requests_without_origin())
            installed = get_global_textmap()

            assert isinstance(installed, TrustAwareTextMapPropagator)
            # The stricter first predicate must not survive underneath the second.
            assert not isinstance(installed.wrapped, TrustAwareTextMapPropagator)
        finally:
            set_global_textmap(original)
