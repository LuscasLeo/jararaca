from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncContextManager, Protocol

from jararaca.core.providers import ProviderSpec

if TYPE_CHECKING:
    from typing_extensions import TypeIs


class AppInterceptor(Protocol):

    def intercept(self) -> AsyncContextManager[None]: ...


class AppInterceptorWithLifecycle(Protocol):

    def lifecycle(self, app: "Microservice") -> AsyncContextManager[None]: ...


def is_interceptor_with_lifecycle(
    interceptor: Any,
) -> "TypeIs[AppInterceptorWithLifecycle]":

    return hasattr(interceptor, "lifecycle")


@dataclass
class Microservice:
    providers: list[type[Any] | ProviderSpec] = field(default_factory=list)
    controllers: list[type] = field(default_factory=list)
    interceptors: list[AppInterceptor] = field(default_factory=list)
