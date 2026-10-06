# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Generation of the TypeScript service class (and hooks) of a REST controller.
"""

import inspect
from io import StringIO
from types import FunctionType, NoneType
from typing import Any

from jararaca.presentation.decorators import HttpMapping, RestController, UseMiddleware
from jararaca.reflect.decorators import resolve_bound_method_decorator
from jararaca.tools.typescript.decorators import MutationEndpoint, QueryEndpoint
from jararaca.tools.typescript.http.endpoint_writer import write_endpoint_method
from jararaca.tools.typescript.http.hooks_writer import write_controller_hooks
from jararaca.tools.typescript.http.middleware_extractor import (
    collect_middleware_parameters,
)
from jararaca.tools.typescript.http.parameter_extractor import extract_parameters
from jararaca.tools.typescript.http.parameters import merge_duplicate_parameters
from jararaca.tools.typescript.type_introspection import extract_all_envolved_types
from jararaca.tools.typescript.type_mapper import (
    get_field_type_for_ts,
    get_generic_type_mapping,
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

        if mapping is None:
            continue

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
        if resolve_bound_method_decorator(
            controller,
            name,
            MutationEndpoint,
            rest_controller.methods_inherit_decorators,
        ):
            decorated_mutations.append((name, member))

        mapped_types.update(extract_all_envolved_types(return_type))

        # For return types, use Output suffix if it's a split model
        return_value_repr = get_field_type_for_ts(return_type, "Output", type_mapping)

        arg_params_spec, parameters_mapped_types = extract_parameters(
            member, rest_controller, mapping, type_mapping
        )

        middleware_params, middleware_mapped_types = collect_middleware_parameters(
            controller, name, rest_controller, mapping, class_usemiddlewares
        )
        parameters_mapped_types.update(middleware_mapped_types)

        # Middleware params first, then controller params
        arg_params_spec = merge_duplicate_parameters(
            middleware_params + arg_params_spec, f"{class_name}.{name}"
        )

        for param in parameters_mapped_types:
            mapped_types.update(extract_all_envolved_types(param))

        write_endpoint_method(
            class_buffer,
            name,
            mapping,
            rest_controller,
            arg_params_spec,
            return_value_repr,
        )

    class_buffer.write("}\n")

    controller_hooks_builder: StringIO | None = None
    if decorated_queries or decorated_mutations:
        controller_hooks_builder = write_controller_hooks(
            class_name, decorated_queries, decorated_mutations
        )

    return class_buffer, mapped_types, controller_hooks_builder
