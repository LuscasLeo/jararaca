# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
import re
from typing import Any

from jararaca.presentation.decorators import HttpMapping, RestController, UseMiddleware
from jararaca.reflect.decorators import resolve_method_decorators
from jararaca.tools.typescript.http.parameter_builders import (
    build_marked_parameter,
    build_parameter_spec,
    is_annotated,
    resolve_annotated_marker,
)
from jararaca.tools.typescript.http.parameters import (
    EXCLUDED_REQUESTS_TYPES,
    HttpParameterSpec,
)
from jararaca.tools.typescript.type_introspection import is_primitive


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

            if is_annotated(parameter_type):
                marker, annotated_type = resolve_annotated_marker(parameter_type)
                built = build_marked_parameter(
                    marker, parameter_name, parameter_type, annotated_type
                )
                if built is not None:
                    spec, referenced_types = built
                    parameters_list.append(spec)
                    mapped_types.update(referenced_types)
                # Dependencies (Depends) are not expanded for middlewares, to avoid
                # infinite recursion
            else:
                # Non-annotated parameters: path parameter when named in the paths,
                # otherwise query parameter if it is a simple type
                mapped_types.add(parameter_type)

                if (
                    # Match both simple parameters {param} and parameters with converters {param:converter}
                    re.search(f"{{{parameter_name}(:.*?)?}}", controller.path)
                    is not None
                    or re.search(f"{{{parameter_name}(:.*?)?}}", mapping.path)
                    is not None
                ):
                    parameters_list.append(
                        build_parameter_spec("path", parameter_name, parameter_type)
                    )
                elif is_primitive(parameter_type):
                    parameters_list.append(
                        build_parameter_spec(
                            "query", parameter_name, parameter_type, split_aware=False
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
