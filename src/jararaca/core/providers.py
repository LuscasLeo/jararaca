from dataclasses import dataclass
from typing import Any, Callable, Generic, Type, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Token(Generic[T]):
    token: Type[T]


@dataclass
class ProviderSpec:
    provide: type[Any] | Token[Any]
    use_value: Any | Token[Any] | None = None
    use_factory: Callable[..., Any] | None = None
    use_class: type[Any] | None = None