from typing import Any, Self, Type, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

IDENTIFIABLE_SCHEMA_T = TypeVar("IDENTIFIABLE_SCHEMA_T")


T_BASEMODEL = TypeVar("T_BASEMODEL", bound=BaseModel)


def recursive_get_dict(obj: Any) -> Any:
    if isinstance(obj, list):
        return [recursive_get_dict(v) for v in obj]
    elif hasattr(obj, "__dict__"):
        return {
            k: recursive_get_dict(v) for k, v in obj.__dict__.items() if k[0] != "_"
        }
    else:
        return obj


class BaseEntity(AsyncAttrs, DeclarativeBase):

    @classmethod
    def from_basemodel(cls, mutation: T_BASEMODEL) -> "Self":
        intersection = set(cls.__annotations__.keys()) & set(
            mutation.model_fields.keys()
        )
        return cls(**{k: getattr(mutation, k) for k in intersection})

    def to_basemodel(self, model: Type[T_BASEMODEL]) -> T_BASEMODEL:
        return model.model_validate(recursive_get_dict(self))
