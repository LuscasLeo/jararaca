# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Trust boundary for inbound W3C trace context.

By default any caller can hand the service a ``traceparent`` and have its root span
adopt that trace id, parent span and sampling decision. On a public edge that lets a
single misbehaving client merge unrelated requests into one enormous trace, or turn
tracing off for its own requests by sending the sampled flag as ``00``.

:class:`InboundTraceContextMiddleware` neutralises the trace context of untrusted callers
before anything reads it, while leaving genuine service to service propagation alone.
"""

import hmac
from typing import Any, Callable, Collection, Iterable, Mapping, TypeGuard

from opentelemetry.context import Context
from opentelemetry.propagate import get_global_textmap, set_global_textmap
from opentelemetry.propagators.textmap import (
    CarrierT,
    Getter,
    Setter,
    TextMapPropagator,
    default_getter,
    default_setter,
)
from starlette.types import ASGIApp, Receive, Scope, Send

TRACE_CONTEXT_HEADERS = ("traceparent", "tracestate", "baggage")
"""Headers that carry inbound trace context and therefore decide the root span's trace."""

CLAIMED_HEADER_PREFIX = "x-claimed-"
"""
Prefix applied to a distrusted caller's trace headers.

They are renamed rather than deleted so the claimed value survives as a span attribute
(``http.request.header.x-claimed-traceparent``) and the frontend to backend correlation
is still queryable, while no propagator will ever read it as a parent.
"""

TrustPredicate = Callable[[Scope], bool]
"""Decides whether the trace context of a connection may be honoured."""

_TRACE_CONTEXT_HEADER_BYTES = frozenset(
    header.encode("latin-1") for header in TRACE_CONTEXT_HEADERS
)
_CLAIMED_HEADER_PREFIX_BYTES = CLAIMED_HEADER_PREFIX.encode("latin-1")


def _get_header(scope: Scope, name: str) -> bytes | None:
    wanted = name.encode("latin-1").lower()

    for header_name, header_value in scope.get("headers") or ():
        if header_name.lower() == wanted:
            return bytes(header_value)

    return None


def trust_no_one() -> TrustPredicate:
    """Never honour inbound trace context. Every request starts its own trace."""

    def predicate(scope: Scope) -> bool:
        return False

    return predicate


def trust_requests_without_origin(
    allowed_origins: Collection[str] = (),
) -> TrustPredicate:
    """
    Distrust anything that looks like a browser, identified by an ``Origin`` header.

    Browsers send ``Origin`` on every cross origin request and on every non ``GET``
    request; server to server HTTP clients normally do not. Origins in *allowed_origins*
    stay trusted, which is useful for a first party frontend that genuinely exports its
    own spans to the same backend.

    This is a heuristic for accidental trace pollution, **not** a security control: a
    caller that wants to be trusted only has to omit the header. Pair it with
    :func:`trust_when_header_matches` or strip the headers at the ingress when the
    boundary has to hold against a hostile client.
    """
    allowed = {origin.encode("latin-1") for origin in allowed_origins}

    def predicate(scope: Scope) -> bool:
        origin = _get_header(scope, "origin")

        if origin is None:
            return True

        return origin in allowed

    return predicate


def trust_when_header_matches(name: str, value: str) -> TrustPredicate:
    """
    Trust callers that present a shared secret, injected by the ingress or a peer service.

    The comparison is constant time. This is the predicate to reach for when the trust
    boundary has to survive a hostile caller.
    """
    expected = value.encode("latin-1")

    def predicate(scope: Scope) -> bool:
        presented = _get_header(scope, name)

        if presented is None:
            return False

        return hmac.compare_digest(presented, expected)

    return predicate


def trust_any_of(*predicates: TrustPredicate) -> TrustPredicate:
    """Trust the connection when any of *predicates* does."""

    def predicate(scope: Scope) -> bool:
        return any(candidate(scope) for candidate in predicates)

    return predicate


def _disarm_trace_headers(
    headers: Iterable[tuple[bytes, bytes]],
) -> list[tuple[bytes, bytes]]:
    return [
        (
            (_CLAIMED_HEADER_PREFIX_BYTES + name.lower(), value)
            if name.lower() in _TRACE_CONTEXT_HEADER_BYTES
            else (name, value)
        )
        for name, value in headers
    ]


def _is_asgi_connection_scope(carrier: Any) -> TypeGuard[Scope]:
    """
    Whether *carrier* is an inbound ASGI connection rather than an internal carrier.

    The trust boundary only applies to connections arriving from outside. Message bus
    headers and other internal carriers are propagated with the same global propagator,
    and distrusting those would silently break trace continuity across the bus.
    """
    return (
        isinstance(carrier, Mapping)
        and carrier.get("type") in ("http", "websocket")
        and "headers" in carrier
    )


class TrustAwareTextMapPropagator(TextMapPropagator):
    """
    A propagator that refuses to extract trace context from untrusted connections.

    This is the order independent half of the trust boundary, and the only thing that
    works against ``FastAPIInstrumentor``. ``instrument_app`` does not install its
    middleware with ``add_middleware``: it replaces ``app.build_middleware_stack`` so
    that ``OpenTelemetryMiddleware`` wraps the whole Starlette stack, including
    ``ServerErrorMiddleware`` and every user middleware. No middleware can therefore run
    before it. What it *does* use is the global propagator, with the ASGI scope as the
    carrier, so replacing that propagator intercepts the extraction wherever it happens::

        install_inbound_trace_boundary(trust=trust_requests_without_origin())
        FastAPIInstrumentor.instrument_app(app)  # order no longer matters

    Injection is delegated untouched: outbound propagation is never the untrusted side.
    """

    def __init__(
        self,
        trust: TrustPredicate,
        wrapped: TextMapPropagator | None = None,
    ) -> None:
        self.trust = trust
        self.wrapped = wrapped if wrapped is not None else get_global_textmap()

    def extract(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        getter: Getter[CarrierT] = default_getter,
    ) -> Context:
        if _is_asgi_connection_scope(carrier) and not self.trust(carrier):
            # No remote parent, so the server span becomes a fresh trace root. The
            # claimed value stays in the request headers and is still recorded as the
            # `http.request.header.traceparent` span attribute.
            return context if context is not None else Context()

        return self.wrapped.extract(carrier, context, getter)

    def inject(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        setter: Setter[CarrierT] = default_setter,
    ) -> None:
        self.wrapped.inject(carrier, context, setter)

    @property
    def fields(self) -> "set[str]":
        return self.wrapped.fields


def install_inbound_trace_boundary(trust: TrustPredicate) -> TextMapPropagator:
    """
    Wrap the global propagator so untrusted connections cannot supply trace context.

    Call it once at startup, before or after instrumenting the app; it does not care.
    Returns the propagator that was replaced, so it can be restored in tests.

    Calling it again replaces the previous boundary rather than stacking a second one on
    top. Stacking would keep the earlier, possibly stricter, predicate in the chain and
    make the new one look like it silently does nothing.
    """
    previous = get_global_textmap()
    underlying = (
        previous.wrapped
        if isinstance(previous, TrustAwareTextMapPropagator)
        else previous
    )

    set_global_textmap(TrustAwareTextMapPropagator(trust, underlying))
    return previous


class InboundTraceContextMiddleware:
    """
    Strip inbound trace context from untrusted callers.

    Pure ASGI rather than ``BaseHTTPMiddleware`` because it only rewrites the scope, and
    because it must be able to sit outside every other middleware.

    This covers jararaca's own extraction, which reads the request headers from inside
    the router. It does **not** cover ``FastAPIInstrumentor``: that patches
    ``build_middleware_stack`` so ``OpenTelemetryMiddleware`` wraps the entire stack, and
    no user middleware can run before it. Use
    :func:`install_inbound_trace_boundary` for that, with the same predicate::

        install_inbound_trace_boundary(trust=trust_requests_without_origin())
        app.add_middleware(
            InboundTraceContextMiddleware,
            trust=trust_requests_without_origin(),
        )
    """

    def __init__(self, app: ASGIApp, *, trust: TrustPredicate) -> None:
        self.app = app
        self.trust = trust

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and not self.trust(scope):
            headers = scope.get("headers")

            if headers:
                scope = {**scope, "headers": _disarm_trace_headers(headers)}

        await self.app(scope, receive, send)
