# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
import re
from typing import Annotated, Any, get_origin

from fastapi.params import Body, Cookie, Depends, Form, Header, Path, Query
from fastapi.security.http import HTTPBase

from jararaca.presentation.decorators import HttpMapping, RestController, UseMiddleware
from jararaca.reflect.decorators import resolve_method_decorators
from jararaca.tools.typescript.decorators import SplitInputOutput
from jararaca.tools.typescript.http.parameters import (
    EXCLUDED_REQUESTS_TYPES,
    HttpParameterSpec,
)
from jararaca.tools.typescript.type_introspection import (
    extract_all_envolved_types,
    is_primitive,
    unwrap_annotated_type,
)
from jararaca.tools.typescript.type_mapper import get_field_type_for_ts


def extract_middleware_parameters(
    middleware_type: type,
    controller: RestController,
    mapping: HttpMapping,
) -> tuple[list[HttpParameterSpec], set[Any]]:
    """
    Extract parameters from a middleware class's intercept method.
    """
    parameters_list: list[HttpParameterSpec] = []
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
                        HttpParameterSpec(
                            type_="header",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(str),
                        )
                    )
                elif isinstance(annotated_type_hook, Cookie):
                    mapped_types.add(str)
                    parameters_list.append(
                        HttpParameterSpec(
                            type_="cookie",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(str),
                        )
                    )
                elif isinstance(annotated_type_hook, Form):
                    mapped_types.add(annotated_type)
                    parameters_list.append(
                        HttpParameterSpec(
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
                        HttpParameterSpec(
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
                        HttpParameterSpec(
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
                        HttpParameterSpec(
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
                        HttpParameterSpec(
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
                        HttpParameterSpec(
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


def collect_middleware_parameters(
    controller: type,
    method_name: str,
    rest_controller: RestController,
    mapping: HttpMapping,
    class_usemiddlewares: list[UseMiddleware],
) -> tuple[list[HttpParameterSpec], set[Any]]:
    """
    Collect the parameters required by every middleware applied to a method:
    controller-level middlewares, class-level ``UseMiddleware`` and method-level
    ``UseMiddleware``, in that order.
    """
    parameters: list[HttpParameterSpec] = []
    mapped_types: set[Any] = set()

    middleware_types: list[type] = [
        *rest_controller.middlewares,
        *(instance.middleware for instance in class_usemiddlewares),
        *(
            instance.middleware
            for instance in resolve_method_decorators(
                controller,
                method_name,
                UseMiddleware,
                rest_controller.methods_inherit_decorators,
            )
        ),
    ]

    for middleware_type in middleware_types:
        middleware_params, middleware_mapped_types = extract_middleware_parameters(
            middleware_type, rest_controller, mapping
        )
        parameters.extend(middleware_params)
        mapped_types.update(middleware_mapped_types)

    return parameters, mapped_types
