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
from typing import Callable, Collection, Iterable

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


class InboundTraceContextMiddleware:
    """
    Strip inbound trace context from untrusted callers.

    Pure ASGI rather than ``BaseHTTPMiddleware`` because it only rewrites the scope, and
    because it must be able to sit outside every other middleware.

    **Ordering matters.** Starlette's ``add_middleware`` inserts at the front of the
    stack, so the *last* middleware added is the outermost one. This must be added after
    ``FastAPIInstrumentor.instrument_app(app)``, otherwise the ASGI instrumentation reads
    the headers before they are rewritten::

        FastAPIInstrumentor.instrument_app(app)
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
