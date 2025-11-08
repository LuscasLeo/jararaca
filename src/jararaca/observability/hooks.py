# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import typing
from contextlib import contextmanager
from typing import Any, Generator, Literal

from jararaca.observability.decorators import (
    AttributeMap,
    AttributeValue,
    TracingContextProvider,
    TracingSpan,
    TracingSpanContext,
    get_tracing_ctx_provider,
)


@contextmanager
def start_span(
    name: str,
    attributes: AttributeMap | None = None,
) -> Generator[None, Any, None]:
    if trace_context_provider := get_tracing_ctx_provider():
        with trace_context_provider.start_span_context(
            trace_name=name, context_attributes=attributes
        ):
            yield
    else:
        yield


def spawn_trace(
    name: str,
    attributes: AttributeMap | None = None,
) -> typing.ContextManager[None]:
    logging.warning(
        "spawn_trace is deprecated, use start_span as context manager instead."
    )
    return start_span(name=name, attributes=attributes)


def add_event(
    name: str,
    attributes: AttributeMap | None = None,
) -> None:

    if trace_context_provider := get_tracing_ctx_provider():
        trace_context_provider.add_event(
            event_name=name,
            event_attributes=attributes,
        )


def set_span_status(status_code: Literal["OK", "ERROR", "UNSET"]) -> None:

    if trace_context_provider := get_tracing_ctx_provider():
        trace_context_provider.set_span_status(status_code=status_code)


def record_exception(
    exception: Exception,
    attributes: AttributeMap | None = None,
    escaped: bool = False,
) -> None:

    if trace_context_provider := get_tracing_ctx_provider():
        trace_context_provider.record_exception(
            exception=exception,
            attributes=attributes,
            escaped=escaped,
        )


def set_span_attribute(
    key: str,
    value: AttributeValue,
) -> None:

    if trace_context_provider := get_tracing_ctx_provider():
        trace_context_provider.set_span_attribute(
            key=key,
            value=value,
        )


def get_tracing_provider() -> TracingContextProvider | None:
    return get_tracing_ctx_provider()


def get_current_span_context() -> TracingSpanContext | None:

    if trace_context_provider := get_tracing_ctx_provider():
        return trace_context_provider.get_current_span_context()
    return None


def get_current_span() -> TracingSpan | None:

    if trace_context_provider := get_tracing_ctx_provider():
        return trace_context_provider.get_current_span()
    return None


def add_span_link(span_context: TracingSpanContext) -> None:

    if trace_context_provider := get_tracing_ctx_provider():
        trace_context_provider.add_link(span_context=span_context)
