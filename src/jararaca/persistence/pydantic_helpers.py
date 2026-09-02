# SPDX-FileCopyrightText: 2026 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Any, Callable, Generic, Sequence, Type, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError
from sqlalchemy import Dialect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

T = TypeVar("T", bound=BaseModel)


class ValidatedJSON(Generic[T]):
    """Lazy, per-row validation wrapper. Never raises on construction."""

    __slots__ = ("_raw", "_model_type", "_cache", "_error", "_attempted")

    def __init__(self, raw: dict[str, Any] | None, model_type: Type[T]) -> None:
        self._raw = raw
        self._model_type = model_type
        self._cache: T | None = None
        self._error: ValidationError | None = None
        self._attempted = False

    def _attempt(self) -> None:
        if self._attempted:
            return
        self._attempted = True
        if self._raw is None:
            return
        try:
            self._cache = self._model_type.model_validate(self._raw)
        except ValidationError as e:
            self._error = e

    @property
    def is_null(self) -> bool:
        return self._raw is None

    @property
    def is_valid(self) -> bool:
        self._attempt()
        return self._error is None

    def get(self) -> T:
        """Raises ValidationError if the stored JSON doesn't match the model."""
        self._attempt()
        if self._error is not None:
            raise self._error

        assert (
            self._cache is not None
        ), "ValidatedJSON: cache should not be None after successful validation"
        return self._cache

    def get_or_none(self) -> T | None:
        """Swallow validation errors, return None instead."""
        self._attempt()
        return self._cache

    @property
    def raw(self) -> dict[str, Any] | None:
        """Escape hatch: the untouched JSON, for logging/inspection/migration."""
        return self._raw


class PydanticJSONB(TypeDecorator[T | ValidatedJSON[T]], Generic[T]):
    impl = JSONB
    cache_ok = True

    def __init__(self, pydantic_type: Type[T], *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pydantic_type = pydantic_type

    def process_bind_param(
        self, value: T | ValidatedJSON[T] | None, dialect: Dialect
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, ValidatedJSON):
            value = value.get()  # eager: raise here if the app tries to write garbage
        if not isinstance(value, self.pydantic_type):
            value = self.pydantic_type.model_validate(value)
        return value.model_dump(mode="json")

    def process_result_value(
        self, value: dict[str, Any] | None, dialect: Dialect
    ) -> ValidatedJSON[T]:
        # never raises — validation deferred until .get()/.get_or_none()/.is_valid is called
        return ValidatedJSON(value, self.pydantic_type)


TModel = TypeVar("TModel", bound=BaseModel)
TEntity = TypeVar("TEntity")


class ValidRow(BaseModel, Generic[TModel]):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    row_id: UUID
    value: TModel


class InvalidRow(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    row_id: UUID
    raw: JsonValue
    error: str  # str(ValidationError) — keep it serializable, don't store the exception object


class SchemaValidationReport(BaseModel, Generic[TModel]):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    valid: list[ValidRow[TModel]]
    invalid: list[InvalidRow]

    @property
    def total(self) -> int:
        return len(self.valid) + len(self.invalid)

    @property
    def invalid_ratio(self) -> float:
        return len(self.invalid) / self.total if self.total else 0.0


def partition_by_schema_validity(
    rows: Sequence[TEntity],
    field_accessor: Callable[[TEntity], "ValidatedJSON[TModel]"],
    id_accessor: Callable[[TEntity], UUID],
) -> SchemaValidationReport[TModel]:
    """
    Splits a sequence of entities into rows whose target JSONB column
    validates against its Pydantic model, and rows that don't.

    Never raises — invalid rows are captured with their raw payload and error,
    so a single malformed row never breaks a bulk fetch.
    """
    valid: list[ValidRow[TModel]] = []
    invalid: list[InvalidRow] = []

    for row in rows:
        wrapper = field_accessor(row)
        row_id = id_accessor(row)

        if wrapper.is_null:
            continue  # decide: skip nulls, or route them to `invalid` — see note below

        if wrapper.is_valid:
            valid.append(ValidRow(row_id=row_id, value=wrapper.get()))
        else:
            invalid.append(
                InvalidRow(row_id=row_id, raw=wrapper.raw, error=str(wrapper._error))
            )

    return SchemaValidationReport(valid=valid, invalid=invalid)
