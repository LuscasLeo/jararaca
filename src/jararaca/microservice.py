import inspect
from contextlib import contextmanager
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
from pydantic import BaseModel

from jararaca.core.providers import ProviderSpec, T, Token
from jararaca.messagebus import Message

if TYPE_CHECKING:
    from typing_extensions import TypeIs


@dataclass
class SchedulerAppContext:
    triggered_at: datetime
    scheduled_to: datetime
    cron_expression: str
    action: Callable[..., Any]
    context_type: Literal["scheduler"] = "scheduler"


@dataclass
class HttpAppContext:
    request: Request
    context_type: Literal["http"] = "http"


@dataclass
class MessageBusAppContext:
    topic: str
    message: Message[BaseModel]
    context_type: Literal["message_bus"] = "message_bus"


@dataclass
class WebSocketAppContext:
    websocket: WebSocket
    context_type: Literal["websocket"] = "websocket"


AppContext = (
    MessageBusAppContext | HttpAppContext | SchedulerAppContext | WebSocketAppContext
)


@runtime_checkable
class AppInterceptor(Protocol):

    def intercept(self, app_context: AppContext) -> AsyncContextManager[None]: ...


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
                    self.get_and_register(provider.use_class, provider.provide)
                elif provider.use_factory:
                    self.get_and_register(provider.use_factory, provider.provide)
            else:
                self.get_and_register(provider, provider)

    def instantiate(self, type_: type[Any] | Callable[..., Any]) -> Any:

        dependencies = self.parse_dependencies(type_)

        evaluated_dependencies = {
            name: self.get_or_register_token_or_type(dependency)
            for name, dependency in dependencies.items()
        }

        instance = type_(**evaluated_dependencies)

        return instance

    def parse_dependencies(
        self, provider: type[Any] | Callable[..., Any]
    ) -> dict[str, type[Any]]:

        signature = inspect.signature(provider)

        parameters = signature.parameters

        return {
            name: self.lookup_parameter_type(parameter)
            for name, parameter in parameters.items()
            if parameter.annotation != inspect.Parameter.empty
        }

    def lookup_parameter_type(self, parameter: inspect.Parameter) -> Any:
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
            return self.get_and_register(item_type, bind_to)

        return cast(T, self.instances_map[bind_to])

    def get_and_register(
        self, item_type: Type[T] | Callable[..., T], bind_to: Any
    ) -> T:
        instance = self.instantiate(item_type)
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
        try:
            current_container_ctx.reset(token)
        except ValueError:
            pass
