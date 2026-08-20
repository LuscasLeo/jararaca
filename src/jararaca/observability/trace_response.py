# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Echo the trace context of a request back to the caller on *every* response.

``setup_fastapi_exception_handler`` only reaches error responses, because that is all an
exception handler ever sees. Handing the header back on successful responses too is what
lets a client, a load test or a support ticket point at the exact trace of a request that
did not fail.
"""

from typing import Iterable

from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jararaca.observability.trace_context import resolve_scope_traceparent

DEFAULT_TRACE_HEADER_NAME = "traceparent"

CORS_EXPOSE_HEADERS = "access-control-expose-headers"
CORS_ALLOW_ORIGIN = "access-control-allow-origin"

__all__ = [
    "TraceResponseHeaderMiddleware",
    "DEFAULT_TRACE_HEADER_NAME",
]


def _has_header(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> bool:
    return any(header_name.lower() == name for header_name, _ in headers)


class TraceResponseHeaderMiddleware:
    """
    Add the trace header to every response, successful ones included.

    Position is not critical: the value is read from the ASGI scope, which the
    observability interceptor populates and which outlives it, so it is still available
    while the response is being sent. The span current at request entry is kept as a
    fallback for connections the interceptor never handled, such as a 404.

    A header already present is left alone, so this composes with
    ``setup_fastapi_exception_handler`` instead of producing a duplicate on error
    responses::

        app.add_middleware(TraceResponseHeaderMiddleware)
        setup_fastapi_exception_handler(app)

    A browser cannot read a non safelisted response header across origins unless CORS
    exposes it. When the response already carries CORS headers, *expose_via_cors* appends
    the header name to ``access-control-expose-headers``. Responses with no CORS headers
    are left untouched.

    That one feature *is* order sensitive: it only works with this middleware **outside**
    ``CORSMiddleware``, otherwise it sends before the CORS headers exist. Since
    ``add_middleware`` makes the last one added outermost, add it after::

        app.add_middleware(CORSMiddleware, ...)
        app.add_middleware(TraceResponseHeaderMiddleware)

    Get it the wrong way round and the trace header is still set, only the exposure is
    lost.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        header_name: str = DEFAULT_TRACE_HEADER_NAME,
        overwrite: bool = False,
        expose_via_cors: bool = True,
    ) -> None:
        self.app = app
        self.header_name = header_name
        self.header_name_bytes = header_name.encode("latin-1").lower()
        self.overwrite = overwrite
        self.expose_via_cors = expose_via_cors

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Captured on the way in: by the time the response is sent the interceptor has
        # exited, so the ambient context is no longer a reliable place to look.
        entry_span = trace.get_current_span()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                self.apply(scope, message, entry_span)

            await send(message)

        await self.app(scope, receive, send_wrapper)

    def apply(self, scope: Scope, message: Message, entry_span: trace.Span) -> None:
        headers: list[tuple[bytes, bytes]] = list(message.get("headers") or [])

        if not self.overwrite and _has_header(headers, self.header_name_bytes):
            return

        traceparent = resolve_scope_traceparent(scope, entry_span)

        if not traceparent:
            return

        headers = [
            (name, value)
            for name, value in headers
            if name.lower() != self.header_name_bytes
        ]
        headers.append((self.header_name_bytes, traceparent.encode("latin-1")))

        if self.expose_via_cors:
            headers = self.expose_header(headers)

        message["headers"] = headers

    def expose_header(
        self, headers: list[tuple[bytes, bytes]]
    ) -> list[tuple[bytes, bytes]]:
        """
        Add the header to ``access-control-expose-headers``, but only when CORS is in
        play. Emitting the directive on a response that has no CORS headers at all would
        just be noise.
        """
        expose_key = CORS_EXPOSE_HEADERS.encode("latin-1")

        if not _has_header(headers, CORS_ALLOW_ORIGIN.encode("latin-1")):
            return headers

        exposed: list[str] = []
        remaining: list[tuple[bytes, bytes]] = []

        for name, value in headers:
            if name.lower() == expose_key:
                exposed.extend(
                    part.strip()
                    for part in value.decode("latin-1").split(",")
                    if part.strip()
                )
            else:
                remaining.append((name, value))

        if not any(name.lower() == self.header_name for name in exposed):
            exposed.append(self.header_name)

        remaining.append((expose_key, ", ".join(exposed).encode("latin-1")))

        return remaining
