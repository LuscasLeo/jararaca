# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
from types import UnionType
from typing import Annotated, Any, get_origin

from fastapi import UploadFile
from fastapi.params import Depends
from fastapi.security.http import HTTPBase
from pydantic import BaseModel, PlainValidator

from jararaca.presentation.decorators import HttpMapping, RestController
from jararaca.tools.typescript.http.parameter_builders import (
    build_marked_parameter,
    build_parameter_spec,
    build_unmarked_parameter,
    is_annotated,
    is_path_parameter,
    resolve_annotated_marker,
)
from jararaca.tools.typescript.http.parameters import (
    EXCLUDED_REQUESTS_TYPES,
    HttpParameterSpec,
)
from jararaca.tools.typescript.type_introspection import (
    extract_all_envolved_types,
    is_primitive,
    is_upload_file_type,
    unwrap_annotated_type,
)


def extract_parameters(
    member: Any,
    controller: RestController,
    mapping: HttpMapping,
    type_mapping: dict[Any, Any] | None = None,
) -> tuple[list[HttpParameterSpec], set[Any]]:
    parameters_list: list[HttpParameterSpec] = []
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

            if is_annotated(parameter_type):
                marker, annotated_type = resolve_annotated_marker(parameter_type)
                built = build_marked_parameter(
                    marker, parameter_name, parameter_type, annotated_type, type_mapping
                )
                if built is not None:
                    spec, referenced_types = built
                    parameters_list.append(spec)
                    mapped_types.update(referenced_types)
                elif isinstance(marker, Depends):
                    depends_hook = marker.dependency or parameter_type.__args__[0]

                    # HTTP authentication dependencies carry no parameters
                    if not isinstance(depends_hook, HTTPBase):
                        rec_parameters, rec_mapped_types = extract_parameters(
                            depends_hook, controller, mapping, type_mapping
                        )
                        mapped_types.update(rec_mapped_types)
                        parameters_list.extend(rec_parameters)
                else:
                    mapped_types.add(annotated_type)
                    parameters_list.append(
                        build_unmarked_parameter(
                            parameter_name,
                            annotated_type,
                            type_mapping,
                            is_path=is_path_parameter(
                                parameter_name, controller, mapping
                            ),
                        )
                    )

            elif inspect.isclass(parameter_type) and issubclass(
                parameter_type, BaseModel
            ):
                mapped_types.update(extract_all_envolved_types(parameter_type))
                parameters_list.append(
                    build_parameter_spec(
                        "body", parameter_name, parameter_type, type_mapping
                    )
                )
            elif parameter_type == UploadFile or is_upload_file_type(parameter_type):
                # UploadFile and list[UploadFile] should be treated as form data
                mapped_types.add(parameter_type)
                parameters_list.append(
                    build_parameter_spec(
                        "form",
                        parameter_name,
                        parameter_type,
                        type_mapping,
                        split_aware=False,
                    )
                )
            else:
                mapped_types.add(parameter_type)
                parameters_list.append(
                    build_unmarked_parameter(
                        parameter_name,
                        parameter_type,
                        type_mapping,
                        is_path=is_path_parameter(parameter_name, controller, mapping),
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
