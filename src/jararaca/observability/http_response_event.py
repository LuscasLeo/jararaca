# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Record the HTTP response of a request as a span event.

The response body only exists at the ASGI layer: by the time Starlette serialises it and
streams ``http.response.start`` / ``http.response.body``, the endpoint has returned and
the observability interceptor has already exited. So this is a middleware rather than
anything the interceptor could do.

An event, not attributes, because it is timestamped: it marks the moment the response
was produced, which is what you want when the interesting part of a slow request is the
gap between the last query and the first byte out.
"""

from typing import Callable, Collection, Sequence

from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jararaca.const import (
    OBSERVABILITY_TRACE_SPAN_HTTP_RESPONSE_MAX_BODY_SIZE_ATTRIBUTE_VALUE,
)
from jararaca.observability.decorators import AttributeValue

DEFAULT_EVENT_NAME = "http.response"

CAPTURABLE_CONTENT_TYPES = ("application/json", "text/")
"""Body capture is limited to textual payloads; binary ones are only ever measured."""

SENSITIVE_HEADERS = ("set-cookie", "authorization", "proxy-authorization")
"""
Response headers recorded as :data:`REDACTED_HEADER_VALUE` instead of their real value.

``set-cookie`` is the one that matters in practice: it is how session credentials leave
the service, and a trace backend is rarely as access controlled as a session store.
"""

REDACTED_HEADER_VALUE = "[redacted]"
"""Stand in value, so a sensitive header still shows up as present."""

BodyRedactor = Callable[[Scope, bytes], bytes]
"""Rewrites a captured body before it reaches the span, for stripping secrets."""


class _ResponseRecord:
    """Accumulates what the ASGI ``send`` channel reveals about a response."""

    def __init__(self, body_budget: int) -> None:
        self.status_code: int | None = None
        self.headers: list[tuple[bytes, bytes]] = []
        self.body = bytearray()
        self.body_size = 0
        self.body_budget = body_budget

    def record_start(self, message: Message) -> None:
        self.status_code = message.get("status")
        self.headers = list(message.get("headers") or [])

    def record_body(self, message: Message) -> None:
        chunk: bytes = message.get("body") or b""
        self.body_size += len(chunk)

        remaining = self.body_budget - len(self.body)

        if remaining > 0:
            self.body.extend(chunk[:remaining])

    @property
    def truncated(self) -> bool:
        return self.body_size > len(self.body)

    def grouped_headers(self) -> dict[str, list[str]]:
        """Header values by lowercased name. A header may legitimately repeat."""
        grouped: dict[str, list[str]] = {}

        for header_name, header_value in self.headers:
            grouped.setdefault(
                header_name.decode("latin-1", errors="ignore").lower(), []
            ).append(header_value.decode("latin-1", errors="ignore"))

        return grouped

    def header(self, name: str) -> str | None:
        wanted = name.encode("latin-1").lower()

        for header_name, header_value in self.headers:
            if header_name.lower() == wanted:
                return header_value.decode("latin-1", errors="ignore")

        return None


class HttpResponseEventMiddleware:
    """
    Add an ``http.response`` event to the active server span for every HTTP response.

    **Requires an ASGI instrumentation that keeps its SERVER span open across response
    streaming** (``FastAPIInstrumentor.instrument_app(app)``). Without one, jararaca's own
    root span has already closed by the time the body is sent, so there is nothing left to
    attach to and this middleware quietly does nothing.

    **Ordering matters, and it is the opposite of
    :class:`~jararaca.observability.inbound_trace.InboundTraceContextMiddleware`.** This one
    has to sit *inside* the instrumentation, so add it *before* ``instrument_app``::

        app.add_middleware(HttpResponseEventMiddleware, capture_body=True)
        FastAPIInstrumentor.instrument_app(app)

    Every response header is recorded as ``http.response.header.<name>``, mirroring the
    ``http.request.header.*`` attributes on the root span. Headers in *sensitive_headers*
    are replaced by :data:`REDACTED_HEADER_VALUE`, and a non empty *capture_headers* acts
    as an allowlist narrowing the set.

    Body capture is off by default. A response body is the single most sensitive thing a
    trace can hold: sign in responses carry freshly issued tokens, and traces are usually
    readable by everyone with Grafana access. Turn it on per service and pass a *redact*
    callable for anything that returns credentials.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        event_name: str = DEFAULT_EVENT_NAME,
        capture_body: bool = False,
        max_body_size: int = (
            OBSERVABILITY_TRACE_SPAN_HTTP_RESPONSE_MAX_BODY_SIZE_ATTRIBUTE_VALUE
        ),
        capture_headers: Collection[str] = (),
        sensitive_headers: Collection[str] = SENSITIVE_HEADERS,
        capturable_content_types: Sequence[str] = CAPTURABLE_CONTENT_TYPES,
        redact: BodyRedactor | None = None,
    ) -> None:
        self.app = app
        self.event_name = event_name
        self.capture_body = capture_body
        self.max_body_size = max_body_size
        self.capture_headers = {name.lower() for name in capture_headers}
        self.sensitive_headers = {name.lower() for name in sensitive_headers}
        self.capturable_content_types = tuple(capturable_content_types)
        self.redact = redact

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Captured on the way in, while the server span is unambiguously current. The
        # send channel runs after the endpoint returned, where the ambient context is a
        # less reliable place to look.
        span = trace.get_current_span()

        if not span.is_recording():
            await self.app(scope, receive, send)
            return

        record = _ResponseRecord(self.max_body_size if self.capture_body else 0)
        emitted = False

        async def send_wrapper(message: Message) -> None:
            nonlocal emitted

            if message["type"] == "http.response.start":
                record.record_start(message)
            elif message["type"] == "http.response.body":
                record.record_body(message)

                if not message.get("more_body", False) and not emitted:
                    emitted = True
                    span.add_event(
                        self.event_name, self.build_attributes(scope, record)
                    )

            await send(message)

        await self.app(scope, receive, send_wrapper)

    def build_attributes(
        self, scope: Scope, record: _ResponseRecord
    ) -> dict[str, AttributeValue]:
        attributes: dict[str, AttributeValue] = {
            "http.response.body.size": record.body_size,
        }

        if record.status_code is not None:
            attributes["http.response.status_code"] = record.status_code

        for header_name, header_values in record.grouped_headers().items():
            if self.capture_headers and header_name not in self.capture_headers:
                continue

            attributes[f"http.response.header.{header_name}"] = self.header_value(
                header_name, header_values
            )

        if self.capture_body and self.is_capturable(record):
            body = bytes(record.body)

            if self.redact is not None:
                body = self.redact(scope, body)

            attributes["http.response.body"] = body.decode(errors="ignore")

            if record.truncated:
                attributes["http.response.body.truncated"] = True

        return attributes

    def header_value(self, name: str, values: list[str]) -> AttributeValue:
        """
        Redaction wins over the allowlist: explicitly asking for ``set-cookie`` is not
        enough to get its value, the header has to be dropped from *sensitive_headers*.
        """
        if name in self.sensitive_headers:
            values = [REDACTED_HEADER_VALUE for _ in values]

        return values[0] if len(values) == 1 else values

    def is_capturable(self, record: _ResponseRecord) -> bool:
        content_type = record.header("content-type") or ""

        return any(
            content_type.startswith(candidate)
            for candidate in self.capturable_content_types
        )


__all__: list[str] = [
    "HttpResponseEventMiddleware",
    "BodyRedactor",
    "DEFAULT_EVENT_NAME",
    "CAPTURABLE_CONTENT_TYPES",
    "SENSITIVE_HEADERS",
    "REDACTED_HEADER_VALUE",
]
