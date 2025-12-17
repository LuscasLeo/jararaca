# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Any, Callable, Protocol, Self, Type, TypeVar

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


RESULT_T = TypeVar("RESULT_T", covariant=True)
ENTITY_T_CONTRA = TypeVar("ENTITY_T_CONTRA", bound="BaseEntity", contravariant=True)


class EntityParserType(Protocol[ENTITY_T_CONTRA, RESULT_T]):

    @classmethod
    def parse_entity(cls, model: ENTITY_T_CONTRA) -> "RESULT_T": ...


EntityParserFunc = Callable[[ENTITY_T_CONTRA], RESULT_T]

BASED_BASE_ENTITY_T = TypeVar("BASED_BASE_ENTITY_T", bound="BaseEntity")


class BaseEntity(AsyncAttrs, DeclarativeBase):

    @classmethod
    def from_basemodel(cls, mutation: T_BASEMODEL) -> "Self":
        intersection = set(cls.__annotations__.keys()) & set(
            mutation.__class__.model_fields.keys()
        )
        return cls(**{k: getattr(mutation, k) for k in intersection})

    def to_basemodel(self, model: Type[T_BASEMODEL]) -> T_BASEMODEL:
        return model.model_validate(recursive_get_dict(self))

    def parse_entity_with_func(
        self, model_cls: EntityParserFunc["Self", RESULT_T]
    ) -> RESULT_T:
        return model_cls(self)

    def parse_entity_with_type(
        self: BASED_BASE_ENTITY_T,
        model_cls: Type[EntityParserType[BASED_BASE_ENTITY_T, RESULT_T]],
    ) -> RESULT_T:
        return model_cls.parse_entity(self)

    def __rshift__(self, model: EntityParserFunc["Self", RESULT_T]) -> RESULT_T:
        return self.parse_entity_with_func(model)

    def __and__(self, model: Type[EntityParserType["Self", RESULT_T]]) -> RESULT_T:
        return self.parse_entity_with_type(model)
