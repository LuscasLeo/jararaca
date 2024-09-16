from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, TypeVar, Union, cast

DECORATED = TypeVar("DECORATED", bound=Union[Callable[..., Awaitable[Any]], type])


metadata_context: ContextVar[dict[str, Any]] = ContextVar("metadata_context")


def get_metadata(key: str) -> Any | None:
    return metadata_context.get({}).get(key)


@contextmanager
def provide_metadata(metadata: dict[str, Any]) -> Any:

    current_metadata = metadata_context.get({})

    token = metadata_context.set({**current_metadata, **metadata})
    try:
        yield
    finally:
        try:
            metadata_context.reset(token)
        except ValueError:
            pass


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
    def get(cls: type) -> "list[SetMetadata]":
        return cast(list[SetMetadata], getattr(cls, SetMetadata.METATADA_LIST, []))

    def __call__(self, cls: DECORATED) -> DECORATED:
        SetMetadata.register_metadata(cls, self)
        return cls
