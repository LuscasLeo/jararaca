# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
import re
import typing
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from io import StringIO
from types import FunctionType, NoneType, UnionType
from typing import (
    IO,
    Annotated,
    Any,
    ClassVar,
    Generator,
    Generic,
    Literal,
    Type,
    TypeVar,
    get_origin,
)
from uuid import UUID

from fastapi import Request, Response, UploadFile
from fastapi.params import Body, Cookie, Depends, Form, Header, Path, Query
from fastapi.security.http import HTTPBase
from pydantic import BaseModel, PlainValidator, RootModel
from pydantic_core import PydanticUndefined

from jararaca.microservice import Microservice
from jararaca.presentation.decorators import HttpMapping, RestController, UseMiddleware
from jararaca.presentation.websocket.decorators import RegisterWebSocketMessage
from jararaca.presentation.websocket.websocket_interceptor import (
    WebSocketMessageWrapper,
)
from jararaca.reflect.decorators import (
    resolve_bound_method_decorator,
    resolve_method_decorators,
)
from jararaca.tools.typescript.decorators import (
    ExposeType,
    MutationEndpoint,
    QueryEndpoint,
    SplitInputOutput,
)

CONSTANT_PATTERN = re.compile(r"^[A-Z_]+$")


def is_constant(name: str) -> bool:
    return CONSTANT_PATTERN.match(name) is not None


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
    if field_type == UploadFile:
        return True

    # Check for list[UploadFile], List[UploadFile], etc.
    origin = get_origin(field_type)
    if origin in (list, frozenset, set):
        args = getattr(field_type, "__args__", ())
        if args and args[0] == UploadFile:
            return True

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


class ParseContext:
    def __init__(self) -> None:
        self.mapped_types: set[Any] = set()


parse_context_ctxvar = ContextVar("parse_context", default=ParseContext())


@contextmanager
def with_parse_context() -> Generator[ParseContext, None, None]:
    parse_context = parse_context_ctxvar.get()
    yield parse_context
    parse_context_ctxvar.set(parse_context)


def use_parse_context() -> ParseContext:
    return parse_context_ctxvar.get()


def snake_to_camel(snake_str: str) -> str:
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def pascal_to_camel(pascal_str: str) -> str:
    """Convert a PascalCase string to camelCase."""
    if not pascal_str:
        return pascal_str
    return pascal_str[0].lower() + pascal_str[1:]


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
        return "number"
    if get_origin(field_type) == ClassVar:
        return get_field_type_for_ts(
            field_type.__args__[0], context_suffix, type_mapping
        )
    if get_origin(field_type) == tuple:
        return f"[{', '.join([get_field_type_for_ts(field, context_suffix, type_mapping) for field in field_type.__args__])}]"
    if get_origin(field_type) == list or get_origin(field_type) == frozenset:
        return f"Array<{get_field_type_for_ts(field_type.__args__[0], context_suffix, type_mapping)}>"
    if get_origin(field_type) == set:
        return f"Array<{get_field_type_for_ts(field_type.__args__[0], context_suffix, type_mapping)}> // Set"
    if get_origin(field_type) == dict:
        return f"{{[key: {get_field_type_for_ts(field_type.__args__[0], context_suffix, type_mapping)}]: {get_field_type_for_ts(field_type.__args__[1], context_suffix, type_mapping)}}}"
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
        return " | ".join([parse_literal_value(x) for x in field_type.__args__])
    if get_origin(field_type) == UnionType or get_origin(field_type) == typing.Union:
        return " | ".join(
            [
                get_field_type_for_ts(x, context_suffix, type_mapping)
                for x in field_type.__args__
            ]
        )
    if (get_origin(field_type) == Annotated) and (len(field_type.__args__) > 0):
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


def is_generic_type(field_type: Any) -> bool:
    return hasattr(field_type, "__parameters__") and len(field_type.__parameters__) > 0


def get_generic_args(field_type: Any) -> Any:
    return field_type.__parameters__


def parse_type_to_typescript_interface(
    basemodel_type: Type[Any],
) -> tuple[set[type], str]:
    """
    Parse a Pydantic model into TypeScript interface(s).

    If the model is decorated with @SplitInputOutput, it generates both Input and Output interfaces.
    Otherwise, it generates a single interface.
    """
    # Check if this model should be split into Input/Output interfaces
    if SplitInputOutput.is_split_model(basemodel_type):
        return parse_split_input_output_interfaces(basemodel_type)

    return parse_single_typescript_interface(basemodel_type)


def parse_split_input_output_interfaces(
    basemodel_type: Type[Any],
) -> tuple[set[type], str]:
    """
    Generate both Input and Output TypeScript interfaces for a split model.
    """
    mapped_types: set[type] = set()
    combined_output = StringIO()

    # Generate Input interface (with optional fields)
    input_mapped_types, input_interface = parse_single_typescript_interface(
        basemodel_type, interface_suffix="Input", force_optional_defaults=True
    )
    mapped_types.update(input_mapped_types)
    combined_output.write(input_interface)

    # Generate Output interface (all fields required as they come from the backend)
    output_mapped_types, output_interface = parse_single_typescript_interface(
        basemodel_type, interface_suffix="Output", force_optional_defaults=False
    )
    mapped_types.update(output_mapped_types)
    combined_output.write(output_interface)

    return mapped_types, combined_output.getvalue()


def parse_single_typescript_interface(
    basemodel_type: Type[Any],
    interface_suffix: str = "",
    force_optional_defaults: bool | None = None,
) -> tuple[set[type], str]:
    """
    Generate a single TypeScript interface for a Pydantic model.

    Args:
        basemodel_type: The Pydantic model class
        interface_suffix: Suffix to add to the interface name (e.g., "Input", "Output")
        force_optional_defaults: If True, fields with defaults are optional. If False, all fields are required.
                                If None, uses the default behavior (fields with defaults are optional).
    """
    string_builder = StringIO()
    mapped_types: set[type] = set()

    inherited_classes = get_inherited_classes(basemodel_type)

    if (orig_type := get_origin(basemodel_type)) is not None:
        mapped_types.update(basemodel_type.__args__)
        mapped_types.add(orig_type)
        return mapped_types, ""

    if hasattr(basemodel_type, "__pydantic_generic_metadata__"):
        metadata = getattr(basemodel_type, "__pydantic_generic_metadata__")
        if metadata.get("origin") is not None:
            mapped_types.add(metadata.get("origin"))

            for arg in metadata.get("args"):
                mapped_types.update(extract_all_envolved_types(arg))

            return mapped_types, ""

    mapped_types.update(inherited_classes)

    if Enum in inherited_classes:
        enum_values = sorted([(x._name_, x.value) for x in basemodel_type])
        return (
            set(),
            f"export enum {basemodel_type.__name__}{interface_suffix} {{\n"
            + "\n ".join([f'\t{name} = "{value}",' for name, value in enum_values])
            + "\n}\n",
        )

    valid_inherited_classes = [
        inherited_class
        for inherited_class in inherited_classes
        if inherited_class is not BaseModel
        if not is_primitive(inherited_class)
    ]

    cls_consts = set(
        field_name for field_name in (f for f in dir(basemodel_type) if is_constant(f))
    )

    inherited_classes_consts_conflict = {
        inherited_class: set(
            field_name
            for field_name in (f for f in dir(inherited_class) if is_constant(f))
        ).intersection(cls_consts)
        for inherited_class in valid_inherited_classes
    }

    # Modify inheritance for split interfaces
    extends_expression = ""
    if len(valid_inherited_classes) > 0:
        extends_base_names = []
        for inherited_class in valid_inherited_classes:
            base_name = get_field_type_for_ts(inherited_class, interface_suffix)
            # If the inherited class is also a split model, use the appropriate suffix
            if SplitInputOutput.is_split_model(inherited_class) and interface_suffix:
                base_name = f"{inherited_class.__name__}{interface_suffix}"

            if inherited_classes_consts_conflict[inherited_class]:
                base_name = "Omit<%s, %s>" % (
                    base_name,
                    " | ".join(
                        sorted(
                            [
                                '"%s"' % field_name
                                for field_name in inherited_classes_consts_conflict[
                                    inherited_class
                                ]
                            ]
                        )
                    ),
                )
            extends_base_names.append(base_name)

        extends_expression = " extends %s" % ", ".join(
            sorted(extends_base_names, key=lambda x: str(x))
        )

    interface_name = f"{basemodel_type.__name__}{interface_suffix}"

    if is_generic_type(basemodel_type):
        generic_args = get_generic_args(basemodel_type)
        string_builder.write(
            f"export interface {interface_name}<{', '.join(sorted([arg.__name__ for arg in generic_args]))}>{extends_expression} {{\n"
        )
    else:
        string_builder.write(
            f"export interface {interface_name}{extends_expression} {{\n"
        )

    if hasattr(basemodel_type, "__annotations__"):
        # Sort fields for consistent output
        annotation_items = sorted(
            basemodel_type.__annotations__.items(), key=lambda x: x[0]
        )

        for field_name, field in annotation_items:
            if field_name in cls_consts:
                continue

            # Check if field should be excluded (private or excluded via Field)
            if should_exclude_field(field_name, field, basemodel_type):
                continue

            # Determine if field is optional based on the force_optional_defaults parameter
            if force_optional_defaults is True:
                # Input interface: fields with defaults are optional
                is_optional = has_default_value(field_name, field, basemodel_type)
            elif force_optional_defaults is False:
                # Output interface: all fields are required (backend provides complete data)
                is_optional = False
            else:
                # Default behavior: fields with defaults are optional
                is_optional = has_default_value(field_name, field, basemodel_type)

            string_builder.write(
                f"  {snake_to_camel(field_name) if not is_constant(field_name) else field_name}{'?' if is_optional else ''}: {get_field_type_for_ts(field, interface_suffix)};\n"
            )
            mapped_types.update(extract_all_envolved_types(field))
            mapped_types.add(field)

    ## Loop over computed fields - sort them for consistent output
    members = sorted(
        inspect.getmembers_static(basemodel_type, lambda a: isinstance(a, property)),
        key=lambda x: x[0],
    )
    for field_name, field in members:
        if hasattr(field, "fget"):
            module_func_name = field.fget.__module__ + "." + field.fget.__qualname__
            if (
                module_func_name
                == basemodel_type.__module__
                + "."
                + basemodel_type.__name__
                + "."
                + field_name
            ):

                return_type = field.fget.__annotations__.get("return")
                if return_type is None:
                    return_type = NoneType

                string_builder.write(
                    f"  {snake_to_camel(field_name)}: {get_field_type_for_ts(return_type, interface_suffix)};\n"
                )
                mapped_types.update(extract_all_envolved_types(return_type))
                mapped_types.add(return_type)

    string_builder.write("}\n")

    return mapped_types, string_builder.getvalue()


def get_inherited_classes(basemodel_type: Type[Any]) -> list[Type[Any]]:
    if not hasattr(basemodel_type, "__bases__"):
        return []
    return [
        base_class
        for base_class in basemodel_type.__bases__
        if get_origin(base_class) is not Generic
        if base_class is not BaseModel
    ]


def write_microservice_to_typescript_interface(
    microservice: Microservice,
) -> str:
    rest_controller_buffer = StringIO()
    mapped_types_set: set[Any] = set()

    websocket_registries: set[RegisterWebSocketMessage] = set()
    mapped_types_set.add(WebSocketMessageWrapper)

    # Add all explicitly exposed types
    mapped_types_set.update(ExposeType.get_all_exposed_types())

    for controller in microservice.controllers:
        rest_controller = RestController.get_last(controller)

        if rest_controller is None:
            continue

        controller_class_strio, types, hooks_strio = (
            write_rest_controller_to_typescript_interface(
                rest_controller,
                controller,
            )
        )

        mapped_types_set.update(types)
        rest_controller_buffer.write(controller_class_strio.getvalue())
        if hooks_strio is not None:
            rest_controller_buffer.write(hooks_strio.getvalue())

        registered = RegisterWebSocketMessage.get_last(controller)

        if registered is not None:
            for message_type in registered.message_types:
                identified_types, text = parse_type_to_typescript_interface(
                    message_type
                )
                websocket_registries.add(registered)
                mapped_types_set.update(identified_types)
                rest_controller_buffer.write(text)

    final_buffer = StringIO()

    final_buffer.write(
        """
/* eslint-disable */

// @ts-nocheck

// noinspection JSUnusedGlobalSymbols

import { HttpService, HttpBackend, HttpBackendRequest, ResponseType, createClassQueryHooks , createClassMutationHooks, createClassInfiniteQueryHooks, paginationModelByFirstArgPaginationFilter, recursiveCamelToSnakeCase } from "@jararaca/core";

function makeFormData(data: Record<string, any>): FormData {
  const formData = new FormData();
  for (const key in data) {
    const value = data[key];
    for (const v of genFormDataValue(value)) {
      formData.append(key, v);
    }
  }
  return formData;
}

function* genFormDataValue(value: any): any {
  if (Array.isArray(value)) {
    // Stringify arrays as JSON
    for (const item of value) {
      // formData.append(`${key}`, item);
      yield* genFormDataValue(item);
    }
  } else if (typeof value === "object" && value.constructor === Object) {
    // Stringify plain objects as JSON
    // formData.append(key, JSON.stringify(value));
    yield JSON.stringify(
      recursiveCamelToSnakeCase(value)
    );
  } else {
    // For primitives (string, number, boolean), append as-is
    yield value;
  }
}

export type WebSocketMessageMap = {
%s
}
"""
        % "\n".join(
            sorted(
                [
                    f'\t"{message.MESSAGE_ID}": {message.__name__};'
                    for registers in websocket_registries
                    for message in registers.message_types
                ]
            )
        )
    )

    processed_types: set[Any] = set()
    backlog: set[Any] = mapped_types_set.copy()

    unordered_types_buffer: list[tuple[type, str]] = []

    while len(backlog) > 0:
        t = backlog.pop()
        if not is_primitive(t):
            with with_parse_context() as parse_context:

                identified_types, text = parse_type_to_typescript_interface(t)
                processed_types.add(t)
                # final_buffer.write(text)
                unordered_types_buffer.append((t, text))
                identified_types.update(parse_context.mapped_types)
                backlog.update(identified_types - processed_types)

    ordered_types_buffer = sorted(unordered_types_buffer, key=lambda x: x[0].__name__)

    for _, text in ordered_types_buffer:
        final_buffer.write(text)

    final_buffer.write(rest_controller_buffer.getvalue())

    return final_buffer.getvalue()


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


def write_rest_controller_to_typescript_interface(
    rest_controller: RestController, controller: type
) -> tuple[StringIO, set[Any], StringIO | None]:

    class_name = controller.__name__

    decorated_queries: list[tuple[str, FunctionType, QueryEndpoint]] = []
    decorated_mutations: list[tuple[str, FunctionType]] = []

    class_buffer = StringIO()

    class_buffer.write(f"export class {class_name} extends HttpService {{\n")

    mapped_types: set[Any] = set()

    # Compute type mapping for generics
    type_mapping = get_generic_type_mapping(controller)

    # Sort members for consistent output
    member_items = sorted(
        inspect.getmembers_static(controller, predicate=inspect.isfunction),
        key=lambda x: x[0],
    )

    class_usemiddlewares = UseMiddleware.get_all_from_type(
        controller, rest_controller.class_inherits_decorators
    )

    for name, member in member_items:
        mapping = resolve_bound_method_decorator(
            controller, name, HttpMapping, rest_controller.methods_inherit_decorators
        )
        effective_member = member

        if mapping is not None:
            return_type = member.__annotations__.get("return")

            if return_type is None:
                return_type = NoneType

            if query_endpoint := resolve_bound_method_decorator(
                controller,
                name,
                QueryEndpoint,
                rest_controller.methods_inherit_decorators,
            ):
                decorated_queries.append((name, member, query_endpoint))
            if MutationEndpoint.is_mutation(effective_member):
                decorated_mutations.append((name, member))

            mapped_types.update(extract_all_envolved_types(return_type))

            # For return types, use Output suffix if it's a split model
            return_value_repr = get_field_type_for_ts(
                return_type, "Output", type_mapping
            )

            arg_params_spec, parametes_mapped_types = extract_parameters(
                member, rest_controller, mapping, type_mapping
            )

            # Collect middleware parameters separately
            middleware_params_list: list[HttpParemeterSpec] = []

            # Extract parameters from controller-level middlewares
            for middleware_type in rest_controller.middlewares:
                middleware_params, middleware_mapped_types = (
                    extract_middleware_parameters(
                        middleware_type, rest_controller, mapping
                    )
                )
                middleware_params_list.extend(middleware_params)
                parametes_mapped_types.update(middleware_mapped_types)

            # Extract parameters from class-level UseMiddleware decorators
            for middleware_instance in class_usemiddlewares:
                middleware_params, middleware_mapped_types = (
                    extract_middleware_parameters(
                        middleware_instance.middleware, rest_controller, mapping
                    )
                )
                middleware_params_list.extend(middleware_params)
                parametes_mapped_types.update(middleware_mapped_types)

            # Extract parameters from method-level middlewares (UseMiddleware)
            # Get the method from the class to access its middleware decorators
            method_middlewares = resolve_method_decorators(
                controller,
                name,
                UseMiddleware,
                rest_controller.methods_inherit_decorators,
            )
            for middleware_instance in method_middlewares:
                middleware_params, middleware_mapped_types = (
                    extract_middleware_parameters(
                        middleware_instance.middleware, rest_controller, mapping
                    )
                )
                middleware_params_list.extend(middleware_params)
                parametes_mapped_types.update(middleware_mapped_types)

            # Combine parameters: middleware params first, then controller params
            arg_params_spec = middleware_params_list + arg_params_spec

            for param in parametes_mapped_types:
                mapped_types.update(extract_all_envolved_types(param))

            class_buffer.write(
                f"\tasync {snake_to_camel(name)}({mount_parametes_arguments(arg_params_spec)}): Promise<{return_value_repr}> {{\n"
            )

            class_buffer.write("\t\t")
            class_buffer.write(
                "const response = await this.httpBackend.request<%s>({ \n"
                % return_value_repr
            )
            if mapping.response_type is not None:
                class_buffer.write(f'\t\t\tresponseType: "{mapping.response_type}",\n')
            class_buffer.write(f'\t\t\tmethod: "{mapping.method}",\n')

            endpoint_path = parse_path_with_params(mapping.path, arg_params_spec)

            # Properly handle path joining to avoid double slashes
            controller_path = rest_controller.path or ""
            # Also apply path transformation to the controller path
            controller_path = parse_path_with_params(controller_path, arg_params_spec)
            path_parts = []

            if controller_path and controller_path.strip("/"):
                path_parts.append(controller_path.strip("/"))
            if endpoint_path and endpoint_path.strip("/"):
                path_parts.append(endpoint_path.strip("/"))

            final_path = "/".join(path_parts) if path_parts else ""
            # Ensure the path starts with a single slash
            formatted_path = f"/{final_path}" if final_path else "/"

            class_buffer.write(f"\t\t\tpath: `{formatted_path}`,\n")

            # Sort path params
            path_params = sorted(
                [param for param in arg_params_spec if param.type_ == "path"],
                key=lambda x: x.name,
            )
            class_buffer.write("\t\t\tpathParams: {\n")
            for param in path_params:
                class_buffer.write(f'\t\t\t\t"{param.name}": {param.name},\n')
            class_buffer.write("\t\t\t},\n")

            # Sort headers
            header_params = sorted(
                [param for param in arg_params_spec if param.type_ == "header"],
                key=lambda x: x.name,
            )
            class_buffer.write("\t\t\theaders: {\n")
            for param in header_params:
                class_buffer.write(f'\t\t\t\t"{param.name}": {param.name},\n')
            class_buffer.write("\t\t\t},\n")

            # Sort query params
            query_params = sorted(
                [param for param in arg_params_spec if param.type_ == "query"],
                key=lambda x: x.name,
            )
            class_buffer.write("\t\t\tquery: {\n")
            for param in query_params:
                class_buffer.write(f'\t\t\t\t"{param.name}": {param.name},\n')
            class_buffer.write("\t\t\t},\n")

            # Check if we need to use FormData (for file uploads or form parameters)
            form_params = [param for param in arg_params_spec if param.type_ == "form"]
            body_param = next((x for x in arg_params_spec if x.type_ == "body"), None)

            if form_params:
                # Use FormData for file uploads and form parameters
                class_buffer.write("\t\t\tbody: makeFormData({\n")

                # Add form parameters (including file uploads)
                for param in form_params:
                    class_buffer.write(f'\t\t\t\t"{param.name}": {param.name},\n')

                # Add body parameter if it exists alongside form params
                if body_param:
                    class_buffer.write(f"\t\t\t\t...{body_param.name},\n")

                class_buffer.write("\t\t\t})\n")
            elif body_param is not None:
                class_buffer.write(f"\t\t\tbody: {body_param.name}\n")
            else:
                class_buffer.write("\t\t\tbody: undefined\n")

            class_buffer.write("\t\t});\n")
            class_buffer.write("\t\treturn response;\n")

            class_buffer.write("\t}\n")

    class_buffer.write("}\n")

    controller_hooks_builder: StringIO | None = None

    if decorated_queries or decorated_mutations:
        controller_hooks_builder = StringIO()
        controller_hooks_builder.write(
            f"export const {pascal_to_camel(class_name)} = {{\n"
        )

        if decorated_queries:
            controller_hooks_builder.write(
                f"\t...createClassQueryHooks({class_name},\n"
            )
            for name, member, _ in decorated_queries:
                controller_hooks_builder.write(f'\t\t"{snake_to_camel(name)}",\n')
            controller_hooks_builder.write("\t),\n")

        if decorated_queries and any(
            query.has_infinite_query for _, _, query in decorated_queries
        ):
            controller_hooks_builder.write(
                f"\t...createClassInfiniteQueryHooks({class_name}, {{\n"
            )
            for name, member, query in decorated_queries:
                if query.has_infinite_query:
                    controller_hooks_builder.write(
                        f'\t\t"{snake_to_camel(name)}": paginationModelByFirstArgPaginationFilter(),\n'
                    )
            controller_hooks_builder.write("\t}),\n")
        if decorated_mutations:
            controller_hooks_builder.write(
                f"\t...createClassMutationHooks({class_name},\n"
            )
            for name, member in decorated_mutations:
                controller_hooks_builder.write(f'\t\t"{snake_to_camel(name)}",\n')
            controller_hooks_builder.write("\t),\n")
        controller_hooks_builder.write("};\n")

    return class_buffer, mapped_types, controller_hooks_builder


EXCLUDED_REQUESTS_TYPES = [Request, Response]


@dataclass
class HttpParemeterSpec:
    type_: Literal["query", "path", "body", "header", "cookie", "form"]
    name: str
    required: bool
    argument_type_str: str


def parse_path_with_params(path: str, parameters: list[HttpParemeterSpec]) -> str:
    # Use a regular expression to match both simple parameters {param} and
    # parameters with converters {param:converter}
    pattern = re.compile(r"{([^:}]+)(?::[^}]*)?}")

    # For each parameter found in the path, replace it with :param format
    for parameter in parameters:
        path = pattern.sub(
            lambda m: f":{m.group(1)}" if m.group(1) == parameter.name else m.group(0),
            path,
        )

    return path


def extract_middleware_parameters(
    middleware_type: type,
    controller: RestController,
    mapping: HttpMapping,
) -> tuple[list[HttpParemeterSpec], set[Any]]:
    """
    Extract parameters from a middleware class's intercept method.
    """
    parameters_list: list[HttpParemeterSpec] = []
    mapped_types: set[Any] = set()

    # Get the intercept method from the middleware class
    if not hasattr(middleware_type, "intercept"):
        return parameters_list, mapped_types

    intercept_method = getattr(middleware_type, "intercept")

    # Use the same logic as extract_parameters but specifically for the intercept method
    try:
        signature = inspect.signature(intercept_method)
        for parameter_name, parameter in signature.parameters.items():
            # Skip 'self' parameter
            if parameter_name == "self":
                continue

            parameter_type = parameter.annotation
            if parameter_type == inspect.Parameter.empty:
                continue

            if parameter_type in EXCLUDED_REQUESTS_TYPES:
                continue

            if get_origin(parameter_type) == Annotated:
                unwrapped_type, all_metadata = unwrap_annotated_type(parameter_type)
                # Look for FastAPI parameter annotations in all metadata layers
                annotated_type_hook = None
                for metadata in all_metadata:
                    if isinstance(
                        metadata, (Header, Cookie, Form, Body, Query, Path, Depends)
                    ):
                        annotated_type_hook = metadata
                        break

                if annotated_type_hook is None and all_metadata:
                    # Fallback to first metadata if no FastAPI annotation found
                    annotated_type_hook = all_metadata[0]

                annotated_type = unwrapped_type
                if isinstance(annotated_type_hook, Header):
                    mapped_types.add(str)
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="header",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(str),
                        )
                    )
                elif isinstance(annotated_type_hook, Cookie):
                    mapped_types.add(str)
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="cookie",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(str),
                        )
                    )
                elif isinstance(annotated_type_hook, Form):
                    mapped_types.add(annotated_type)
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="form",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(annotated_type),
                        )
                    )
                elif isinstance(annotated_type_hook, Body):
                    mapped_types.update(extract_all_envolved_types(parameter_type))
                    # For body parameters, use Input suffix if it's a split model
                    context_suffix = (
                        "Input"
                        if (
                            inspect.isclass(parameter_type)
                            and hasattr(parameter_type, "__dict__")
                            and SplitInputOutput.is_split_model(parameter_type)
                        )
                        else ""
                    )
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="body",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                parameter_type, context_suffix
                            ),
                        )
                    )
                elif isinstance(annotated_type_hook, Query):
                    mapped_types.add(parameter_type)
                    # For query parameters, use Input suffix if it's a split model
                    context_suffix = (
                        "Input"
                        if (
                            inspect.isclass(parameter_type)
                            and hasattr(parameter_type, "__dict__")
                            and SplitInputOutput.is_split_model(parameter_type)
                        )
                        else ""
                    )
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="query",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                parameter_type, context_suffix
                            ),
                        )
                    )
                elif isinstance(annotated_type_hook, Path):
                    mapped_types.add(parameter_type)
                    # For path parameters, use Input suffix if it's a split model
                    context_suffix = (
                        "Input"
                        if (
                            inspect.isclass(parameter_type)
                            and hasattr(parameter_type, "__dict__")
                            and SplitInputOutput.is_split_model(parameter_type)
                        )
                        else ""
                    )
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="path",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                parameter_type, context_suffix
                            ),
                        )
                    )
                elif isinstance(annotated_type_hook, Depends):
                    # For Dependencies, recursively extract parameters
                    depends_hook = (
                        annotated_type_hook.dependency or parameter_type.__args__[0]
                    )
                    if isinstance(depends_hook, HTTPBase):
                        # Skip HTTP authentication dependencies
                        pass
                    else:
                        # TODO: We might need to recursively extract from dependencies
                        # For now, skip to avoid infinite recursion
                        pass
            else:
                # Handle non-annotated parameters - check if they are path parameters
                mapped_types.add(parameter_type)

                # Check if parameter matches path parameters in controller or method paths
                if (
                    # Match both simple parameters {param} and parameters with converters {param:converter}
                    re.search(f"{{{parameter_name}(:.*?)?}}", controller.path)
                    is not None
                    or re.search(f"{{{parameter_name}(:.*?)?}}", mapping.path)
                    is not None
                ):
                    # This is a path parameter
                    context_suffix = (
                        "Input"
                        if (
                            inspect.isclass(parameter_type)
                            and hasattr(parameter_type, "__dict__")
                            and SplitInputOutput.is_split_model(parameter_type)
                        )
                        else ""
                    )
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="path",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                parameter_type, context_suffix
                            ),
                        )
                    )
                elif is_primitive(parameter_type):
                    # Default to query parameters for simple types that aren't in the path
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="query",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(parameter_type),
                        )
                    )

    except (ValueError, TypeError):
        # If we can't inspect the signature, return empty
        pass

    return parameters_list, mapped_types


def mount_parametes_arguments(parameters: list[HttpParemeterSpec]) -> str:
    return ", ".join(
        [f"{parameter.name}: {parameter.argument_type_str}" for parameter in parameters]
    )


def extract_parameters(
    member: Any,
    controller: RestController,
    mapping: HttpMapping,
    type_mapping: dict[Any, Any] | None = None,
) -> tuple[list[HttpParemeterSpec], set[Any]]:
    parameters_list: list[HttpParemeterSpec] = []
    mapped_types: set[Any] = set()
    if get_origin(member) is UnionType:
        for arg in member.__args__:
            if is_primitive(arg):
                continue
            rec_parameters, rec_mapped_types = extract_parameters(
                arg, controller, mapping, type_mapping
            )
            mapped_types.update(rec_mapped_types)
            parameters_list.extend(rec_parameters)
        return parameters_list, mapped_types

    if is_primitive(member):

        if get_origin(member) is Annotated:
            unwrapped_type, all_metadata = unwrap_annotated_type(member)
            if (
                plain_validator := next(
                    (x for x in all_metadata if isinstance(x, PlainValidator)),
                    None,
                )
            ) is not None:
                mapped_types.add(plain_validator.json_schema_input_type)
                return parameters_list, mapped_types
            return extract_parameters(unwrapped_type, controller, mapping, type_mapping)
        return parameters_list, mapped_types

    if hasattr(member, "__bases__"):
        for base in member.__bases__:
            # if base is not BaseModel:
            rec_parameters, rec_mapped_types = extract_parameters(
                base, controller, mapping, type_mapping
            )
            mapped_types.update(rec_mapped_types)
            parameters_list.extend(rec_parameters)

    if hasattr(member, "__annotations__"):
        for parameter_name, parameter_type in member.__annotations__.items():
            if parameter_name == "return":
                continue
            if parameter_type in EXCLUDED_REQUESTS_TYPES:
                continue

            # Resolve generic type
            if type_mapping and parameter_type in type_mapping:
                parameter_type = type_mapping[parameter_type]

            if get_origin(parameter_type) == Annotated:
                unwrapped_type, all_metadata = unwrap_annotated_type(parameter_type)
                # Look for FastAPI parameter annotations in all metadata layers
                annotated_type_hook = None
                for metadata in all_metadata:
                    if isinstance(
                        metadata, (Header, Cookie, Form, Body, Query, Path, Depends)
                    ):
                        annotated_type_hook = metadata
                        break

                if annotated_type_hook is None and all_metadata:
                    # Fallback to first metadata if no FastAPI annotation found
                    annotated_type_hook = all_metadata[0]

                annotated_type = unwrapped_type
                if isinstance(annotated_type_hook, Header):
                    mapped_types.add(str)
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="header",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                str, "", type_mapping
                            ),
                        )
                    )
                elif isinstance(annotated_type_hook, Cookie):
                    mapped_types.add(str)
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="cookie",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                str, "", type_mapping
                            ),
                        )
                    )
                elif isinstance(annotated_type_hook, Form):
                    mapped_types.add(annotated_type)
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="form",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                annotated_type, "", type_mapping
                            ),
                        )
                    )
                elif isinstance(annotated_type_hook, Body):
                    mapped_types.update(extract_all_envolved_types(parameter_type))
                    # For body parameters, use Input suffix if it's a split model
                    context_suffix = (
                        "Input"
                        if (
                            inspect.isclass(parameter_type)
                            and hasattr(parameter_type, "__dict__")
                            and SplitInputOutput.is_split_model(parameter_type)
                        )
                        else ""
                    )
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="body",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                parameter_type, context_suffix, type_mapping
                            ),
                        )
                    )
                elif isinstance(annotated_type_hook, Query):
                    mapped_types.add(parameter_type)
                    # For query parameters, use Input suffix if it's a split model
                    context_suffix = (
                        "Input"
                        if (
                            inspect.isclass(parameter_type)
                            and hasattr(parameter_type, "__dict__")
                            and SplitInputOutput.is_split_model(parameter_type)
                        )
                        else ""
                    )
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="query",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                parameter_type, context_suffix, type_mapping
                            ),
                        )
                    )
                elif isinstance(annotated_type_hook, Path):
                    mapped_types.add(parameter_type)
                    # For path parameters, use Input suffix if it's a split model
                    context_suffix = (
                        "Input"
                        if (
                            inspect.isclass(parameter_type)
                            and hasattr(parameter_type, "__dict__")
                            and SplitInputOutput.is_split_model(parameter_type)
                        )
                        else ""
                    )
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="path",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                parameter_type, context_suffix, type_mapping
                            ),
                        )
                    )

                elif isinstance(annotated_type_hook, Depends):
                    depends_hook = (
                        annotated_type_hook.dependency or parameter_type.__args__[0]
                    )

                    if isinstance(depends_hook, HTTPBase):
                        ...

                    else:
                        rec_parameters, rec_mapped_types = extract_parameters(
                            depends_hook, controller, mapping, type_mapping
                        )
                        mapped_types.update(rec_mapped_types)
                        parameters_list.extend(rec_parameters)
                elif (
                    re.search(f":{parameter_name}(?:/|$)", controller.path) is not None
                ):
                    mapped_types.add(annotated_type)
                    # For path parameters, use Input suffix if it's a split model
                    context_suffix = (
                        "Input"
                        if (
                            inspect.isclass(annotated_type)
                            and hasattr(annotated_type, "__dict__")
                            and SplitInputOutput.is_split_model(annotated_type)
                        )
                        else ""
                    )
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="path",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                annotated_type, context_suffix, type_mapping
                            ),
                        )
                    )
                else:
                    mapped_types.add(annotated_type)
                    # Special handling for UploadFile and list[UploadFile] - should be treated as form data
                    if is_upload_file_type(annotated_type):
                        parameters_list.append(
                            HttpParemeterSpec(
                                type_="form",
                                name=parameter_name,
                                required=True,
                                argument_type_str=get_field_type_for_ts(
                                    annotated_type, "", type_mapping
                                ),
                            )
                        )
                    else:
                        # For default parameters (treated as query), use Input suffix if it's a split model
                        context_suffix = (
                            "Input"
                            if (
                                inspect.isclass(annotated_type)
                                and hasattr(annotated_type, "__dict__")
                                and SplitInputOutput.is_split_model(annotated_type)
                            )
                            else ""
                        )
                        parameters_list.append(
                            HttpParemeterSpec(
                                type_="query",
                                name=parameter_name,
                                required=True,
                                argument_type_str=get_field_type_for_ts(
                                    annotated_type, context_suffix, type_mapping
                                ),
                            )
                        )

            elif inspect.isclass(parameter_type) and issubclass(
                parameter_type, BaseModel
            ):
                mapped_types.update(extract_all_envolved_types(parameter_type))
                # For BaseModel parameters, use Input suffix if it's a split model
                context_suffix = (
                    "Input" if SplitInputOutput.is_split_model(parameter_type) else ""
                )
                parameters_list.append(
                    HttpParemeterSpec(
                        type_="body",
                        name=parameter_name,
                        required=True,
                        argument_type_str=get_field_type_for_ts(
                            parameter_type, context_suffix, type_mapping
                        ),
                    )
                )
            elif parameter_type == UploadFile or is_upload_file_type(parameter_type):
                # UploadFile and list[UploadFile] should be treated as form data
                mapped_types.add(parameter_type)
                parameters_list.append(
                    HttpParemeterSpec(
                        type_="form",
                        name=parameter_name,
                        required=True,
                        argument_type_str=get_field_type_for_ts(
                            parameter_type, "", type_mapping
                        ),
                    )
                )
            elif (
                # Match both simple parameters {param} and parameters with converters {param:converter}
                re.search(f"{{{parameter_name}(:.*?)?}}", controller.path) is not None
                or re.search(f"{{{parameter_name}(:.*?)?}}", mapping.path) is not None
            ):
                mapped_types.add(parameter_type)
                # For path parameters, use Input suffix if it's a split model
                context_suffix = (
                    "Input"
                    if (
                        inspect.isclass(parameter_type)
                        and hasattr(parameter_type, "__dict__")
                        and SplitInputOutput.is_split_model(parameter_type)
                    )
                    else ""
                )
                parameters_list.append(
                    HttpParemeterSpec(
                        type_="path",
                        name=parameter_name,
                        required=True,
                        argument_type_str=get_field_type_for_ts(
                            parameter_type, context_suffix, type_mapping
                        ),
                    )
                )
            else:
                mapped_types.add(parameter_type)
                # Special handling for UploadFile and list[UploadFile] - should be treated as form data
                if is_upload_file_type(parameter_type):
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="form",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                parameter_type, "", type_mapping
                            ),
                        )
                    )
                else:
                    # For default parameters (treated as query), use Input suffix if it's a split model
                    context_suffix = (
                        "Input"
                        if (
                            inspect.isclass(parameter_type)
                            and hasattr(parameter_type, "__dict__")
                            and SplitInputOutput.is_split_model(parameter_type)
                        )
                        else ""
                    )
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="query",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(
                                parameter_type, context_suffix, type_mapping
                            ),
                        )
                    )

            if inspect.isclass(parameter_type) and not is_primitive(parameter_type):
                signature = inspect.signature(parameter_type)

                parameter_members = signature.parameters

                for _, parameter_type in parameter_members.items():
                    if is_primitive(parameter_type.annotation):
                        if get_origin(parameter_type.annotation) is not None:
                            if get_origin(parameter_type.annotation) == Annotated:
                                unwrapped_type, all_metadata = unwrap_annotated_type(
                                    parameter_type.annotation
                                )
                                plain_validator = next(
                                    (
                                        x
                                        for x in all_metadata
                                        if isinstance(x, PlainValidator)
                                    ),
                                    None,
                                )
                                if plain_validator is not None:
                                    mapped_types.add(
                                        plain_validator.json_schema_input_type
                                    )
                            else:
                                args = parameter_type.annotation.__args__
                                mapped_types.update(args)
                        else:
                            continue
                    _, types = extract_parameters(
                        parameter_type.annotation, controller, mapping, type_mapping
                    )
                    mapped_types.update(types)

    if hasattr(member, "__args__"):
        for arg in member.__args__:

            rec_parameters, rec_mapped_types = extract_parameters(
                arg, controller, mapping, type_mapping
            )
            mapped_types.update(rec_mapped_types)
            parameters_list.extend(rec_parameters)

    return parameters_list, mapped_types


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
                    *[extract_all_envolved_types(arg) for arg in field_type.__args__]
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
        for arg in field_type.__args__:
            mapped_types.update(extract_all_envolved_types(arg))

    return mapped_types
