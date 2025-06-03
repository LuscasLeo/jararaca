import inspect
import logging
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    AsyncContextManager,
    Callable,
    Generator,
    Literal,
    Protocol,
    Type,
    cast,
    get_origin,
    runtime_checkable,
)

from fastapi import Request, WebSocket

from jararaca.core.providers import ProviderSpec, T, Token
from jararaca.messagebus import MessageOf
from jararaca.messagebus.message import Message
from jararaca.reflect.controller_inspect import ControllerMemberReflect

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from typing_extensions import TypeIs


@dataclass
class SchedulerTransactionData:
    triggered_at: datetime
    scheduled_to: datetime
    cron_expression: str
    context_type: Literal["scheduler"] = "scheduler"


@dataclass
class HttpTransactionData:
    request: Request
    context_type: Literal["http"] = "http"


@dataclass
class MessageBusTransactionData:
    topic: str
    message: MessageOf[Message]
    context_type: Literal["message_bus"] = "message_bus"


@dataclass
class WebSocketTransactionData:
    websocket: WebSocket
    context_type: Literal["websocket"] = "websocket"


TransactionData = (
    MessageBusTransactionData
    | HttpTransactionData
    | SchedulerTransactionData
    | WebSocketTransactionData
)


@dataclass
class AppTransactionContext:
    transaction_data: TransactionData
    controller_member_reflect: ControllerMemberReflect


AppContext = AppTransactionContext
"""
Alias for AppTransactionContext, used for compatibility with existing code.
"""


app_transaction_context_var = ContextVar[AppTransactionContext]("app_context")


def use_app_transaction_context() -> AppTransactionContext:
    """
    Returns the current application transaction context.
    This function is used to access the application transaction context in the context of an application transaction.
    If no context is set, it raises a LookupError.
    """

    return app_transaction_context_var.get()


def use_app_tx_ctx_data() -> TransactionData:
    """
    Returns the transaction data from the current app transaction context.
    This function is used to access the transaction data in the context of an application transaction.
    """

    return use_app_transaction_context().transaction_data


use_app_context = use_app_tx_ctx_data
"""Alias for use_app_tx_ctx_data, used for compatibility with existing code."""


@contextmanager
def provide_app_context(
    app_context: AppTransactionContext,
) -> Generator[None, None, None]:
    token = app_transaction_context_var.set(app_context)
    try:
        yield
    finally:
        with suppress(ValueError):
            app_transaction_context_var.reset(token)


@runtime_checkable
class AppInterceptor(Protocol):

    def intercept(
        self, app_context: AppTransactionContext
    ) -> AsyncContextManager[None]: ...


class AppInterceptorWithLifecycle(Protocol):

    def lifecycle(
        self, app: "Microservice", container: "Container"
    ) -> AsyncContextManager[None]: ...


def is_interceptor_with_lifecycle(
    interceptor: Any,
) -> "TypeIs[AppInterceptorWithLifecycle]":

    return hasattr(interceptor, "lifecycle")


@dataclass
class Microservice:
    providers: list[type[Any] | ProviderSpec] = field(default_factory=list)
    controllers: list[type] = field(default_factory=list)
    interceptors: list[AppInterceptor | Callable[..., AppInterceptor]] = field(
        default_factory=list
    )


@dataclass
class InstantiationNode:
    property_name: str
    parent: "InstantiationNode | None" = None
    source_type: Any | None = None
    target_type: Any | None = None


instantiation_vector_ctxvar = ContextVar[list[InstantiationNode]](
    "instantiation_vector", default=[]
)


def print_instantiation_vector(
    instantiation_vector: list[InstantiationNode],
) -> None:
    """
    Prints the instantiation vector for debugging purposes.
    """
    for node in instantiation_vector:
        print(
            f"Property: {node.property_name}, Source: {node.source_type}, Target: {node.target_type}"
        )


@contextmanager
def span_instantiation_vector(
    instantiation_node: InstantiationNode,
) -> Generator[None, None, None]:
    """
    Context manager to track instantiation nodes in a vector.
    This is useful for debugging and tracing instantiation paths.
    """
    current_vector = list(instantiation_vector_ctxvar.get())
    current_vector.append(instantiation_node)
    token = instantiation_vector_ctxvar.set(current_vector)
    try:
        yield
    finally:
        with suppress(ValueError):
            instantiation_vector_ctxvar.reset(token)


class Container:

    def __init__(self, app: Microservice) -> None:
        self.instances_map: dict[Any, Any] = {}
        self.app = app

    def fill_providers(self, after_interceptors: bool) -> None:
        for provider in (
            app
            for app in self.app.providers
            if after_interceptors == app.after_interceptors
        ):
            if isinstance(provider, ProviderSpec):
                if provider.use_value:
                    self.instances_map[provider.provide] = provider.use_value
                elif provider.use_class:
                    self._get_and_register(provider.use_class, provider.provide)
                elif provider.use_factory:
                    self._get_and_register(provider.use_factory, provider.provide)
            else:
                self._get_and_register(provider, provider)

    def _instantiate(self, type_: type[Any] | Callable[..., Any]) -> Any:

        dependencies = self._parse_dependencies(type_)

        evaluated_dependencies: dict[str, Any] = {}
        for name, dependency in dependencies.items():
            with span_instantiation_vector(
                InstantiationNode(
                    property_name=name,
                    source_type=type_,
                    target_type=dependency,
                )
            ):
                evaluated_dependencies[name] = self.get_or_register_token_or_type(
                    dependency
                )

        instance = type_(**evaluated_dependencies)

        return instance

    def _parse_dependencies(
        self, provider: type[Any] | Callable[..., Any]
    ) -> dict[str, type[Any]]:

        vector = instantiation_vector_ctxvar.get()
        try:
            signature = inspect.signature(provider)
        except ValueError:
            print("VECTOR:", vector)
            print_instantiation_vector(vector)
            raise

        parameters = signature.parameters

        return {
            name: self._lookup_parameter_type(parameter)
            for name, parameter in parameters.items()
            if parameter.annotation != inspect.Parameter.empty
        }

    def _lookup_parameter_type(self, parameter: inspect.Parameter) -> Any:
        if parameter.annotation == inspect.Parameter.empty:
            raise Exception(f"Parameter {parameter.name} has no type annotation")

        # if is a Annotated type, return the second argument

        annotation = parameter.annotation
        if get_origin(annotation) is Annotated:

            if len(annotation.__metadata__) == 0:
                raise Exception(f"Parameter {parameter.name} has no type annotation")

            return annotation.__metadata__[0]

        return parameter.annotation

    def get_or_register_token_or_type(
        self, token_or_type: Type[T] | Token[T] | Callable[..., T]
    ) -> T:

        if token_or_type in self.instances_map:
            return cast(T, self.instances_map[token_or_type])

        item_type: Type[T] | Callable[..., T]
        bind_to: Token[T] | Type[T] | Callable[..., T]

        if isinstance(token_or_type, Token):
            item_type = token_or_type.type_
            bind_to = token_or_type
        else:
            item_type = bind_to = token_or_type

        if token_or_type not in self.instances_map:
            return self._get_and_register(item_type, bind_to)

        return cast(T, self.instances_map[bind_to])

    def _get_and_register(
        self, item_type: Type[T] | Callable[..., T], bind_to: Any
    ) -> T:
        instance = self._instantiate(item_type)
        self.register(instance, bind_to)
        return cast(T, instance)

    def register(self, instance: T, bind_to: Any) -> None:
        self.instances_map[bind_to] = instance

    def get_by_type(self, token: Type[T]) -> T:
        return self.get_or_register_token_or_type(token)

    def get_by_token(self, token: Token[T]) -> T:
        return self.get_or_register_token_or_type(token)


current_container_ctx = ContextVar[Container]("current_container")


def use_current_container() -> Container:
    return current_container_ctx.get()


@contextmanager
def provide_container(container: Container) -> Generator[None, None, None]:
    token = current_container_ctx.set(container)
    try:
        yield
    finally:
        with suppress(ValueError):
            current_container_ctx.reset(token)


class ShutdownState(Protocol):

    def request_shutdown(self) -> None: ...

    def is_shutdown_requested(self) -> bool: ...


shutdown_state_ctx = ContextVar[ShutdownState]("shutdown_state")


def is_shutting_down() -> bool:
    """
    Check if the application is in the process of shutting down.
    """
    return shutdown_state_ctx.get().is_shutdown_requested()


def request_shutdown() -> None:
    """
    Request the application to shut down.
    This will set the shutdown event, allowing the application to gracefully shut down.
    """
    shutdown_state_ctx.get().request_shutdown()


@contextmanager
def provide_shutdown_state(
    state: ShutdownState,
) -> Generator[None, None, None]:
    """
    Context manager to provide the shutdown state.
    This is used to manage the shutdown event for the application.
    """

    token = shutdown_state_ctx.set(state)
    try:
        yield
    finally:
        with suppress(ValueError):
            shutdown_state_ctx.reset(token)


__all__ = [
    "AppTransactionContext",
    "AppInterceptor",
    "AppInterceptorWithLifecycle",
    "Container",
    "Microservice",
    "SchedulerTransactionData",
    "WebSocketTransactionData",
    "app_transaction_context_var",
    "current_container_ctx",
    "provide_app_context",
    "provide_container",
    "use_app_context",
    "use_current_container",
    "HttpTransactionData",
    "MessageBusTransactionData",
    "is_interceptor_with_lifecycle",
    "AppContext",
]
