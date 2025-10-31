import inspect
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from functools import wraps
from typing import (
    Any,
    AsyncContextManager,
    Awaitable,
    Callable,
    ContextManager,
    Generator,
    Protocol,
    TypeVar,
)

from jararaca.microservice import AppTransactionContext

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


class TracingContextProvider(Protocol):

    def __call__(
        self, trace_name: str, context_attributes: dict[str, str]
    ) -> ContextManager[Any]: ...


class TracingContextProviderFactory(Protocol):

    def root_setup(
        self, app_context: AppTransactionContext
    ) -> AsyncContextManager[None]: ...

    def provide_provider(
        self, app_context: AppTransactionContext
    ) -> TracingContextProvider: ...


tracing_ctx_provider_ctxv = ContextVar[TracingContextProvider]("tracing_ctx_provider")


@contextmanager
def provide_tracing_ctx_provider(
    ctx_provider: TracingContextProvider,
) -> Generator[None, None, None]:

    token = tracing_ctx_provider_ctxv.set(ctx_provider)
    try:
        yield
    finally:
        with suppress(ValueError):
            tracing_ctx_provider_ctxv.reset(token)


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
        decorated: F,
    ) -> F:

        @wraps(decorated)
        async def wrapper(
            *args: Any,
            **kwargs: Any,
        ) -> Any:

            if ctx_provider := get_tracing_ctx_provider():
                with ctx_provider(
                    self.trace_name,
                    self.trace_mapper(**kwargs),
                ):
                    return await decorated(*args, **kwargs)

            return await decorated(*args, **kwargs)

        return wrapper  # type: ignore[return-value]


C = TypeVar("C", bound=type)


class TracedClass:
    """
    Class decorator that automatically applies tracing to all async methods in a class.

    Usage:
        @TracedClass()
        class MyService:
            async def method1(self) -> str:
                return "hello"

            async def method2(self, x: int) -> int:
                return x * 2

            def sync_method(self) -> str:  # Not traced
                return "sync"
    """

    def __init__(
        self,
        trace_name_prefix: str | None = None,
        trace_mapper: Callable[..., dict[str, str]] = default_trace_mapper,
        include_private: bool = False,
        exclude_methods: set[str] | None = None,
    ):
        """
        Initialize the TracedClass decorator.

        Args:
            trace_name_prefix: Prefix for trace names. If None, uses class name.
            trace_mapper: Function to map method arguments to trace attributes.
            include_private: Whether to trace private methods (starting with _).
            exclude_methods: Set of method names to exclude from tracing.
        """
        self.trace_name_prefix = trace_name_prefix
        self.trace_mapper = trace_mapper
        self.include_private = include_private
        self.exclude_methods = exclude_methods or set()

    def __call__(self, cls: C) -> C:
        """Apply tracing to all async methods in the class."""

        # Use class name as prefix if not provided
        trace_prefix = self.trace_name_prefix or cls.__name__

        # Get all methods in the class
        for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            # Skip if method should be excluded
            if name in self.exclude_methods:
                continue

            # Skip private methods unless explicitly included
            if name.startswith("_") and not self.include_private:
                continue

            # Only trace async methods
            if inspect.iscoroutinefunction(method):
                trace_name = f"{trace_prefix}.{name}"
                traced_method = TracedFunc(trace_name, self.trace_mapper)(method)
                setattr(cls, name, traced_method)

        return cls


def traced_class(
    trace_name_prefix: str | None = None,
    trace_mapper: Callable[..., dict[str, str]] = default_trace_mapper,
    include_private: bool = False,
    exclude_methods: set[str] | None = None,
) -> Callable[[C], C]:
    """
    Functional interface for TracedClass decorator.

    Usage:
        @traced_class(trace_name_prefix="MyService")
        class MyService:
            async def method1(self) -> str:
                return "hello"
    """
    return TracedClass(
        trace_name_prefix=trace_name_prefix,
        trace_mapper=trace_mapper,
        include_private=include_private,
        exclude_methods=exclude_methods,
    )


__all__ = [
    "TracingContextProvider",
    "TracingContextProviderFactory",
    "provide_tracing_ctx_provider",
    "get_tracing_ctx_provider",
    "default_trace_mapper",
    "TracedFunc",
    "TracedClass",
    "traced_class",
]
