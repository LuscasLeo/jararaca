# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
import typing
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from types import NoneType, UnionType
from typing import IO, Annotated, Any, Generic, Literal, Type, TypeVar, get_origin
from uuid import UUID

from fastapi import UploadFile
from pydantic import BaseModel, PlainValidator
from pydantic_core import PydanticUndefined


def unwrap_annotated_type(field_type: Any) -> tuple[Any, list[Any]]:
    """
    Recursively unwrap Annotated types to find the real underlying type.

    Args:
        field_type: The type to unwrap, which may be deeply nested Annotated types

    Returns:
        A tuple of (unwrapped_type, all_metadata) where:
        - unwrapped_type is the final non-Annotated type
        - all_metadata is a list of all metadata from all Annotated layers
    """
    all_metadata = []
    current_type = field_type

    while get_origin(current_type) == Annotated:
        # Collect metadata from current layer
        if hasattr(current_type, "__metadata__"):
            all_metadata.extend(current_type.__metadata__)

        # Move to the next inner type
        if hasattr(current_type, "__args__") and len(current_type.__args__) > 0:
            current_type = current_type.__args__[0]
        else:
            break

    return current_type, all_metadata


def is_upload_file_type(field_type: Any) -> bool:
    """
    Check if a type is UploadFile or a list/array of UploadFile.

    Args:
        field_type: The type to check

    Returns:
        True if it's UploadFile or list[UploadFile], False otherwise
    """

    if get_origin(field_type) == UnionType or get_origin(field_type) == typing.Union:
        args = getattr(field_type, "__args__", ())
        if args and len(args) == 2 and args[1] is type(None):
            return is_upload_file_type(args[0])

    if field_type == UploadFile:
        return True

    # Check for list[UploadFile], List[UploadFile], etc.
    origin = get_origin(field_type)
    if inspect.isclass(origin) and issubclass(origin, UploadFile):
        return True

    if origin in (list, frozenset, set, Annotated):
        args = getattr(field_type, "__args__", ())
        if args:
            return is_upload_file_type(args[0])

    return False


def should_exclude_field(
    field_name: str, field_type: Any, basemodel_type: Type[Any]
) -> bool:
    """
    Check if a field should be excluded from TypeScript interface generation.

    Args:
        field_name: The name of the field
        field_type: The type annotation of the field
        basemodel_type: The BaseModel class containing the field

    Returns:
        True if the field should be excluded, False otherwise
    """
    # Check if field is private (starts with underscore)
    if field_name.startswith("_"):
        return True

    # Check if field has Pydantic Field annotation and is excluded via model_fields
    if (
        hasattr(basemodel_type, "model_fields")
        and field_name in basemodel_type.model_fields
    ):
        field_info = basemodel_type.model_fields[field_name]

        # Check if field is excluded via Field(exclude=True)
        if hasattr(field_info, "exclude") and field_info.exclude:
            return True

        # Check if field is marked as private via Field(..., alias=None) pattern
        if (
            hasattr(field_info, "alias")
            and field_info.alias is None
            and field_name.startswith("_")
        ):
            return True

    # Check for Annotated types with Field metadata
    if get_origin(field_type) == Annotated:
        unwrapped_type, all_metadata = unwrap_annotated_type(field_type)
        for metadata in all_metadata:
            # Check if this is a Pydantic Field by looking for expected attributes
            if hasattr(metadata, "exclude") or hasattr(metadata, "alias"):
                # Check if Field has exclude=True
                if hasattr(metadata, "exclude") and metadata.exclude:
                    return True
                # Check for private fields with alias=None
                if (
                    hasattr(metadata, "alias")
                    and metadata.alias is None
                    and field_name.startswith("_")
                ):
                    return True

    # Check for Field instances assigned as default values
    # This handles cases like: field_name: str = Field(exclude=True)
    if (
        hasattr(basemodel_type, "__annotations__")
        and field_name in basemodel_type.__annotations__
    ):
        # Check if there's a default value that's a Field instance
        if hasattr(basemodel_type, field_name):
            default_value = getattr(basemodel_type, field_name, None)
            # Check if default value has Field-like attributes (duck typing approach)
            if default_value is not None and hasattr(default_value, "exclude"):
                if getattr(default_value, "exclude", False):
                    return True
            # Check for private fields with alias=None in default Field
            if (
                default_value is not None
                and hasattr(default_value, "alias")
                and getattr(default_value, "alias", None) is None
                and field_name.startswith("_")
            ):
                return True

    return False


def has_default_value(
    field_name: str, field_type: Any, basemodel_type: Type[Any]
) -> bool:
    """
    Check if a field has a default value (making it optional in TypeScript).

    Args:
        field_name: The name of the field
        field_type: The type annotation of the field
        basemodel_type: The BaseModel class containing the field

    Returns:
        True if the field has a default value, False otherwise
    """
    # Skip literal types as they don't have defaults in the traditional sense
    if get_origin(field_type) is Literal:
        return False

    # Check if field has default in model_fields (standard Pydantic way)
    if (
        hasattr(basemodel_type, "model_fields")
        and field_name in basemodel_type.model_fields
    ):
        field_info = basemodel_type.model_fields[field_name]
        if field_info.default is not PydanticUndefined:
            return True

    # Check for Field instances assigned as default values
    # This handles cases like: field_name: str = Field(default="value")
    if (
        hasattr(basemodel_type, "__annotations__")
        and field_name in basemodel_type.__annotations__
    ):
        if hasattr(basemodel_type, field_name):
            default_value = getattr(basemodel_type, field_name, None)
            # Check if it's a Field instance with a default
            if default_value is not None and hasattr(default_value, "default"):
                # Check if the Field has a default value set
                field_default = getattr(default_value, "default", PydanticUndefined)
                if field_default is not PydanticUndefined:
                    return True

    # Check for non-Field default values assigned directly to class attributes
    # This handles cases like: field_name: str = "default_value"
    if hasattr(basemodel_type, field_name):
        default_value = getattr(basemodel_type, field_name, None)
        # If it's not a Field instance but has a value, it's a default
        if (
            default_value is not None
            and not hasattr(default_value, "exclude")  # Not a Field instance
            and not hasattr(default_value, "alias")
        ):  # Not a Field instance
            return True

    # Check for Annotated types with Field metadata that have defaults
    if get_origin(field_type) == Annotated:
        unwrapped_type, all_metadata = unwrap_annotated_type(field_type)
        for metadata in all_metadata:
            # Check if this is a Pydantic Field with a default
            if hasattr(metadata, "default") and hasattr(
                metadata, "exclude"
            ):  # Ensure it's a Field
                field_default = getattr(metadata, "default", PydanticUndefined)
                if field_default is not PydanticUndefined:
                    return True

    return False


def is_generic_type(field_type: Any) -> bool:
    return hasattr(field_type, "__parameters__") and len(field_type.__parameters__) > 0


def get_type_args(field_type: Any) -> tuple[Any, ...]:
    """
    ``__args__`` of a typing construct (``list[int]``, ``A | B``, ``Literal[...]``...).

    Static checkers narrow ``field_type`` to ``type[object]`` after equality checks and
    then reject ``__args__``; going through ``Any`` keeps the dynamic access explicit.
    """
    args: tuple[Any, ...] = getattr(field_type, "__args__")
    return args


def get_generic_args(field_type: Any) -> Any:
    return field_type.__parameters__


def get_inherited_classes(basemodel_type: Type[Any]) -> list[Type[Any]]:
    if not hasattr(basemodel_type, "__bases__"):
        return []
    return [
        base_class
        for base_class in basemodel_type.__bases__
        if get_origin(base_class) is not Generic
        if base_class is not BaseModel
    ]


def is_primitive(field_type: Any) -> bool:
    return (
        field_type
        in [
            str,
            int,
            float,
            bool,
            NoneType,
            UUID,
            Decimal,
            date,
            datetime,
            object,
            Enum,
            set,
            UploadFile,
            IO,
            time,
            Any,
        ]
        or get_origin(field_type)
        in [list, dict, tuple, Literal, UnionType, Annotated, set]
        or isinstance(
            field_type,
            (
                str,
                int,
                float,
                bool,
                NoneType,
                UUID,
                Decimal,
                date,
                datetime,
                Enum,
                TypeVar,
                set,
            ),
        )
    )


def extract_all_envolved_types(field_type: Any) -> set[Any]:
    mapped_types: set[Any] = set()

    if field_type is None:
        return mapped_types

    if is_primitive(field_type):
        if get_origin(field_type) is not None:
            if get_origin(field_type) == Annotated:
                unwrapped_type, all_metadata = unwrap_annotated_type(field_type)
                plain_validator = next(
                    (x for x in all_metadata if isinstance(x, PlainValidator)),
                    None,
                )
                if plain_validator is not None:
                    mapped_types.add(plain_validator.json_schema_input_type)
                    return mapped_types
            else:
                mapped_types.update(
                    *[
                        extract_all_envolved_types(arg)
                        for arg in get_type_args(field_type)
                    ]
                )
                return mapped_types

    if inspect.isclass(field_type):
        if hasattr(field_type, "__pydantic_generic_metadata__"):
            metadata = getattr(field_type, "__pydantic_generic_metadata__")
            if metadata.get("origin") is not None:
                mapped_types.add(metadata.get("origin"))
            else:
                mapped_types.add(field_type)
            for arg in metadata.get("args"):
                mapped_types.update(extract_all_envolved_types(arg))
        else:
            mapped_types.add(field_type)

        if hasattr(field_type, "__annotations__"):
            for member in field_type.__annotations__.values():
                mapped_types.update(extract_all_envolved_types(member))

    if hasattr(field_type, "__args__"):
        for arg in get_type_args(field_type):
            mapped_types.update(extract_all_envolved_types(arg))

    return mapped_types
