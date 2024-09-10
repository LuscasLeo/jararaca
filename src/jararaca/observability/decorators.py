from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import (
    Any,
    AsyncContextManager,
    Awaitable,
    Callable,
    ContextManager,
    Generator,
    ParamSpec,
    Protocol,
    TypeVar,
)

from jararaca.microservice import AppContext

P = ParamSpec("P")
R = TypeVar("R")


class TracingContextProvider(Protocol):

    def __call__(
        self, trace_name: str, context_attributes: dict[str, str]
    ) -> ContextManager[Any]: ...


class TracingContextProviderFactory(Protocol):

    def root_setup(self, app_context: AppContext) -> AsyncContextManager[None]: ...

    def provide_provider(self, app_context: AppContext) -> TracingContextProvider: ...


tracing_ctx_provider_ctxv = ContextVar[TracingContextProvider]("tracing_ctx_provider")


@contextmanager
def provide_tracing_ctx_provider(
    ctx_provider: TracingContextProvider,
) -> Generator[None, None, None]:

    token = tracing_ctx_provider_ctxv.set(ctx_provider)
    try:
        yield
    finally:
        try:
            tracing_ctx_provider_ctxv.reset(token)
        except ValueError:
            pass


def get_tracing_ctx_provider() -> TracingContextProvider | None:
    return tracing_ctx_provider_ctxv.get(None)


def default_trace_mapper(*args: Any, **kwargs: Any) -> dict[str, str]:
    return {
        "args": str(args),
        "kwargs": str(kwargs),
    }


class TracedFunc:

    def __init__(
        self,
        trace_name: str,
        trace_mapper: Callable[..., dict[str, str]] = default_trace_mapper,
    ):
        self.trace_name = trace_name
        self.trace_mapper = trace_mapper

    def __call__(
        self,
        decorated: Callable[P, Awaitable[R]],
    ) -> Callable[P, Awaitable[R]]:

        @wraps(decorated)
        async def wrapper(
            *args: P.args,
            **kwargs: P.kwargs,
        ) -> R:

            if ctx_provider := get_tracing_ctx_provider():
                with ctx_provider(
                    self.trace_name,
                    self.trace_mapper(**kwargs),
                ):
                    return await decorated(*args, **kwargs)

            return await decorated(*args, **kwargs)

        return wrapper
