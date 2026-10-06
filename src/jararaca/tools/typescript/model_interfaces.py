# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
from enum import Enum
from io import StringIO
from types import NoneType
from typing import Any, Type, cast, get_origin

from pydantic import BaseModel

from jararaca.tools.typescript.decorators import SplitInputOutput
from jararaca.tools.typescript.naming import is_constant, snake_to_camel
from jararaca.tools.typescript.type_introspection import (
    extract_all_envolved_types,
    get_generic_args,
    get_inherited_classes,
    has_default_value,
    is_generic_type,
    is_primitive,
    should_exclude_field,
)
from jararaca.tools.typescript.type_mapper import get_field_type_for_ts


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
        enum_casted = cast(Type[Enum], basemodel_type)
        enum_values = sorted([(x._name_, x.value) for x in enum_casted])
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
