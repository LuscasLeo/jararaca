from contextlib import contextmanager
from typing import Any, Generator

from jararaca.observability.decorators import AttributeMap, get_tracing_ctx_provider


@contextmanager
def spawn_trace(
    name: str,
    attributes: AttributeMap | None = None,
) -> Generator[None, Any, None]:

    if trace_context_provider := get_tracing_ctx_provider():
        with trace_context_provider(trace_name=name, context_attributes=attributes):
            yield
    else:
        yield


spawn_trace
