from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, TypeVar, Union, cast

DECORATED = TypeVar("DECORATED", bound=Union[Callable[..., Awaitable[Any]], type])


@dataclass
class ControllerInstanceMetadata:
    value: Any
    inherited: bool


metadata_context: ContextVar[Mapping[str, ControllerInstanceMetadata]] = ContextVar(
    "metadata_context"
)


def get_metadata(key: str) -> ControllerInstanceMetadata | None:
    return metadata_context.get({}).get(key)


def get_metadata_value(key: str, default: Any | None = None) -> Any:
    metadata = get_metadata(key)
    if metadata is None:
        return default
    return metadata.value


def get_all_metadata() -> Mapping[str, ControllerInstanceMetadata]:
    return metadata_context.get({})


@contextmanager
def provide_metadata(metadata: Mapping[str, ControllerInstanceMetadata]) -> Any:

    current_metadata = metadata_context.get({})

    token = metadata_context.set({**current_metadata, **metadata})
    try:
        yield
    finally:
        with suppress(ValueError):
            metadata_context.reset(token)


class SetMetadata:
    def __init__(self, key: str, value: Any) -> None:
        self.key = key
        self.value = value

    METATADA_LIST = "__metadata_list__"

    @staticmethod
    def register_metadata(cls: DECORATED, value: "SetMetadata") -> None:
        metadata_list = getattr(cls, SetMetadata.METATADA_LIST, [])
        metadata_list.append(value)
        setattr(cls, SetMetadata.METATADA_LIST, metadata_list)

    @staticmethod
    def get(cls: DECORATED) -> "list[SetMetadata]":
        return cast(list[SetMetadata], getattr(cls, SetMetadata.METATADA_LIST, []))

    def __call__(self, cls: DECORATED) -> DECORATED:
        SetMetadata.register_metadata(cls, self)
        return cls
