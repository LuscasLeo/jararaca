# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Reading the trace context of the current request back out, for echoing to the caller.

Kept apart from :mod:`jararaca.observability.providers.otel` because that module pulls in
the OTLP exporters, which are an optional dependency. Everything here needs only
``opentelemetry-api``.
"""

from typing import Any, Mapping

from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from jararaca.observability.constants import TRACEPARENT_KEY

__all__ = ["format_traceparent", "resolve_scope_traceparent"]


def format_traceparent(span_context: trace.SpanContext) -> str:
    """
    Render *span_context* as a W3C ``traceparent`` header value.

    An invalid span context yields an empty string so that callers can treat "no trace"
    and "no header" as the same case. The propagator does the formatting instead of an
    f-string here so the version prefix stays tied to the spec implementation.
    """
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(
        carrier,
        context=trace.set_span_in_context(trace.NonRecordingSpan(span_context)),
    )
    return carrier.get(TRACEPARENT_KEY, "")


def resolve_scope_traceparent(
    scope: Mapping[str, Any], fallback_span: trace.Span | None = None
) -> str:
    """
    The ``traceparent`` to echo back for the connection described by *scope*.

    The observability interceptor publishes it into the ASGI scope, which is the reliable
    source: the scope outlives the interceptor, so it is still there while the response is
    being sent. *fallback_span* covers connections the interceptor never handled, such as
    a 404 or a failure raised in middleware; pass the span that was current when the
    request entered, since the ambient context is gone by response time.
    """
    scope_value = scope.get(TRACEPARENT_KEY)

    if isinstance(scope_value, str) and scope_value:
        return scope_value

    span = fallback_span if fallback_span is not None else trace.get_current_span()

    return format_traceparent(span.get_span_context())
