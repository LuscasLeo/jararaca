from contextlib import contextmanager
from typing import Any, Generator, Literal

from jararaca.observability.decorators import (
    AttributeMap,
    TracingContextProvider,
    get_tracing_ctx_provider,
)


@contextmanager
def spawn_trace(
    name: str,
    attributes: AttributeMap | None = None,
) -> Generator[None, Any, None]:

    if trace_context_provider := get_tracing_ctx_provider():
        with trace_context_provider.start_trace_context(
            trace_name=name, context_attributes=attributes
        ):
            yield
    else:
        yield


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


def get_tracing_provider() -> TracingContextProvider | None:
    return get_tracing_ctx_provider()
