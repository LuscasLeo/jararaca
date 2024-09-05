import inspect
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Generic, Type, TypeVar, cast, get_origin

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
            name: self.instances_map[dependency]
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

    def get_or_instantiate(self, token: Type[T]) -> T:
        if token not in self.instances_map:
            self.instantiate(token, token)

        return cast(T, self.instances_map[token])

    def get_by_type(self, token: Type[T]) -> T:
        return self.get_or_instantiate(token)

    def get_by_token(self, token: Token[T]) -> T:
        return self.get_or_instantiate(token.token)
