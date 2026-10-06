# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
import re
from types import UnionType
from typing import Annotated, Any, get_origin

from fastapi import UploadFile
from fastapi.params import Body, Cookie, Depends, Form, Header, Path, Query
from fastapi.security.http import HTTPBase
from pydantic import BaseModel, PlainValidator

from jararaca.presentation.decorators import HttpMapping, RestController
from jararaca.tools.typescript.decorators import SplitInputOutput
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
from jararaca.tools.typescript.type_mapper import get_field_type_for_ts


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
                        HttpParameterSpec(
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
                        HttpParameterSpec(
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
                        HttpParameterSpec(
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
                        HttpParameterSpec(
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
                        HttpParameterSpec(
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
                        HttpParameterSpec(
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
                        HttpParameterSpec(
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
                            HttpParameterSpec(
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
                            HttpParameterSpec(
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
                    HttpParameterSpec(
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
                    HttpParameterSpec(
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
                    HttpParameterSpec(
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
                        HttpParameterSpec(
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
                        HttpParameterSpec(
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
