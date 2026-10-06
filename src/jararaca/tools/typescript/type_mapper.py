# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
import typing
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from types import NoneType, UnionType
from typing import Annotated, Any, ClassVar, Literal, TypeVar, get_origin
from uuid import UUID

from fastapi import Response, UploadFile
from pydantic import PlainValidator, RootModel

from jararaca.tools.typescript.decorators import SplitInputOutput
from jararaca.tools.typescript.parse_context import use_parse_context
from jararaca.tools.typescript.type_introspection import (
    get_type_args,
    unwrap_annotated_type,
)


def parse_literal_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, Enum):
        use_parse_context().mapped_types.add(value.__class__)
        return f"{value.__class__.__name__}.{value.name}"
    if isinstance(value, str):
        # Properly escape quotes for TypeScript string literals
        escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped_value}"'
    if isinstance(value, float):
        return str(value)
    if isinstance(value, bool):
        # Ensure Python's True/False are properly converted to JavaScript's true/false
        return "true" if value else "false"
    # Special handling for Python symbols that might appear in literal types
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    return "unknown"


def get_generic_type_mapping(controller_class: type) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    if not hasattr(controller_class, "__orig_bases__"):
        return mapping

    for base in controller_class.__orig_bases__:
        origin = get_origin(base)
        if origin and hasattr(origin, "__parameters__"):
            args = typing.get_args(base)
            params = origin.__parameters__
            if len(args) == len(params):
                for param, arg in zip(params, args):
                    mapping[param] = arg
    return mapping


def get_field_type_for_ts(
    field_type: Any,
    context_suffix: str = "",
    type_mapping: dict[Any, Any] | None = None,
) -> Any:
    """
    Convert a Python type to its TypeScript equivalent.

    Args:
        field_type: The Python type to convert
        context_suffix: Suffix for split models (e.g., "Input", "Output")
        type_mapping: Mapping of TypeVars to concrete types
    """
    if type_mapping and field_type in type_mapping:
        return get_field_type_for_ts(
            type_mapping[field_type], context_suffix, type_mapping
        )

    # Handle RootModel types - use the wrapped type directly
    if inspect.isclass(field_type) and issubclass(field_type, RootModel):
        # For concrete RootModel subclasses, get the wrapped type from annotations
        if (
            hasattr(field_type, "__annotations__")
            and "root" in field_type.__annotations__
        ):
            wrapped_type = field_type.__annotations__["root"]
            return get_field_type_for_ts(wrapped_type, context_suffix, type_mapping)

        # For parameterized RootModel[T] types, get the type from pydantic metadata
        if hasattr(field_type, "__pydantic_generic_metadata__"):
            metadata = field_type.__pydantic_generic_metadata__
            if metadata.get("origin") is RootModel and metadata.get("args"):
                # Get the first (and only) type argument
                wrapped_type = metadata["args"][0]
                return get_field_type_for_ts(wrapped_type, context_suffix, type_mapping)

        return "unknown"

    if field_type is Response:
        return "unknown"
    if field_type is Any:
        return "any"
    if field_type == UploadFile:
        return "File"
    if field_type == time:
        return "string"
    if field_type == date:
        return "string"
    if field_type == datetime:
        return "string"
    if field_type == NoneType:
        return "null"
    if field_type == UUID:
        return "string"
    if field_type == str:
        return "string"
    if field_type == int:
        return "number"
    if field_type == float:
        return "number"
    if field_type == bool:
        return "boolean"
    if field_type == Decimal:
        return "string"
    if get_origin(field_type) == ClassVar:
        return get_field_type_for_ts(
            get_type_args(field_type)[0], context_suffix, type_mapping
        )
    if get_origin(field_type) == tuple:
        return f"[{', '.join([get_field_type_for_ts(field, context_suffix, type_mapping) for field in get_type_args(field_type)])}]"
    if get_origin(field_type) == list or get_origin(field_type) == frozenset:
        return f"Array<{get_field_type_for_ts(get_type_args(field_type)[0], context_suffix, type_mapping)}>"
    if get_origin(field_type) == set:
        return f"Array<{get_field_type_for_ts(get_type_args(field_type)[0], context_suffix, type_mapping)}> // Set"
    if get_origin(field_type) == dict:
        return f"{{[key: {get_field_type_for_ts(get_type_args(field_type)[0], context_suffix, type_mapping)}]: {get_field_type_for_ts(get_type_args(field_type)[1], context_suffix, type_mapping)}}}"
    if inspect.isclass(field_type):
        if not hasattr(field_type, "__pydantic_generic_metadata__"):
            # Check if this is a split model and use appropriate suffix
            if SplitInputOutput.is_split_model(field_type) and context_suffix:
                return f"{field_type.__name__}{context_suffix}"
            return field_type.__name__
        pydantic_metadata = getattr(field_type, "__pydantic_generic_metadata__")

        name = (
            pydantic_metadata.get("origin").__name__
            if pydantic_metadata.get("origin") is not None
            else field_type.__name__
        )

        # Check if this is a split model and use appropriate suffix
        if SplitInputOutput.is_split_model(field_type) and context_suffix:
            name = f"{field_type.__name__}{context_suffix}"

        args = pydantic_metadata.get("args")

        if len(args) > 0:
            return "%s<%s>" % (
                name,
                ", ".join(
                    [
                        get_field_type_for_ts(arg, context_suffix, type_mapping)
                        for arg in args
                    ]
                ),
            )

        return name

    if hasattr(field_type, "__class__") and field_type.__class__ is TypeVar:
        return field_type.__name__
    if get_origin(field_type) == Literal:
        return " | ".join([parse_literal_value(x) for x in get_type_args(field_type)])
    if get_origin(field_type) == UnionType or get_origin(field_type) == typing.Union:
        return " | ".join(
            [
                get_field_type_for_ts(x, context_suffix, type_mapping)
                for x in get_type_args(field_type)
            ]
        )
    if (get_origin(field_type) == Annotated) and (len(get_type_args(field_type)) > 0):
        unwrapped_type, all_metadata = unwrap_annotated_type(field_type)

        if (
            plain_validator := next(
                (x for x in all_metadata if isinstance(x, PlainValidator)),
                None,
            )
        ) is not None:
            return get_field_type_for_ts(
                plain_validator.json_schema_input_type, context_suffix, type_mapping
            )
        return get_field_type_for_ts(unwrapped_type, context_suffix, type_mapping)
    return "unknown"
