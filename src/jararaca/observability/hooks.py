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
from jararaca.observability.providers.otel import use_message_bus_metrics


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


def record_message_sent(topic: str, message_type: str, message_category: str) -> None:
    """
    Record a message sent to the message bus.

    Args:
        topic: The message topic
        message_type: The message type (task/event)
        message_category: The message category
    """
    try:

        if metrics := use_message_bus_metrics():
            metrics.messages_sent_counter.add(
                1,
                {
                    "topic": topic,
                    "message_type": message_type,
                    "message_category": message_category,
                },
            )
    except Exception:
        # Silently ignore metrics errors to not break functionality
        pass


def record_message_processed(
    topic: str, message_type: str, message_category: str, success: bool
) -> None:
    """
    Record a message processed from the message bus.

    Args:
        topic: The message topic
        message_type: The message type (task/event)
        message_category: The message category
        success: Whether the message was processed successfully
    """
    try:

        if metrics := use_message_bus_metrics():
            attributes = {
                "topic": topic,
                "message_type": message_type,
                "message_category": message_category,
            }

            if success:
                metrics.messages_processed_counter.add(1, attributes)
            else:
                metrics.messages_failed_counter.add(1, attributes)
    except Exception:
        # Silently ignore metrics errors to not break functionality
        pass
