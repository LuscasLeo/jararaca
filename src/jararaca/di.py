import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Annotated, Any, Callable, Generator, Type, cast, get_origin

from jararaca.core.providers import ProviderSpec, T, Token
from jararaca.microservice import Microservice


class Container:

    def __init__(self, app: Microservice) -> None:
        self.instances_map: dict[Any, Any] = {}

        self.fill_providers(app.providers)

    def fill_providers(self, providers: list[type[Any] | ProviderSpec]) -> None:
        for provider in providers:
            if isinstance(provider, ProviderSpec):
                if provider.use_value:
                    self.instances_map[provider.provide] = provider.use_value
                elif provider.use_class:
                    self.instantiate(provider.use_class, provider.provide)
                elif provider.use_factory:
                    self.instantiate(provider.use_factory, provider.provide)
            else:
                self.instantiate(provider, provider)

    def instantiate(self, type_: type[Any] | Callable[..., Any], bind_to: Any) -> Any:

        dependencies = self.parse_dependencies(type_)

        evaluated_dependencies = {
            name: self.get_or_instantiate(dependency)
            for name, dependency in dependencies.items()
        }

        self.instances_map[bind_to] = type_(**evaluated_dependencies)

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

    def get_or_instantiate(self, token_or_type: Type[T] | Token[T]) -> T:

        item_type: Type[T]
        bind_to: Token[T] | Type[T]

        if isinstance(token_or_type, Token):
            item_type = token_or_type.type_
            bind_to = token_or_type
        else:
            item_type = bind_to = token_or_type

        if token_or_type not in self.instances_map:
            self.instantiate(item_type, bind_to)

        return cast(T, self.instances_map[bind_to])

    def get_by_type(self, token: Type[T]) -> T:
        return self.get_or_instantiate(token)

    def get_by_token(self, token: Token[T]) -> T:
        return self.get_or_instantiate(token)


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
