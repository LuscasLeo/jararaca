from dataclasses import dataclass, field
from typing import Any, AsyncContextManager, Callable, Protocol

from jararaca.core.providers import ProviderSpec


class AppInterceptor(Protocol):

    def intercept(self) -> AsyncContextManager[None]: ...


@dataclass
class Microservice:
    providers: list[type[Any] | ProviderSpec] = field(default_factory=list)
    controllers: list[type] = field(default_factory=list)
    interceptors: list[AppInterceptor] = field(default_factory=list)
