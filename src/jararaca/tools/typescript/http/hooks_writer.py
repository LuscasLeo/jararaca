# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Emission of the ``createClass*Hooks`` helpers (react-query bindings) of a controller.
"""

from io import StringIO
from types import FunctionType

from jararaca.tools.typescript.decorators import QueryEndpoint
from jararaca.tools.typescript.naming import pascal_to_camel, snake_to_camel


def write_controller_hooks(
    class_name: str,
    decorated_queries: list[tuple[str, FunctionType, QueryEndpoint]],
    decorated_mutations: list[tuple[str, FunctionType]],
) -> StringIO:
    controller_hooks_builder = StringIO()

    # Methods that are both query and mutation are exposed under dedicated namespaces
    method_names_with_query = {name for name, _, _ in decorated_queries}
    method_names_with_mutation = {name for name, _ in decorated_mutations}
    methods_with_both = method_names_with_query & method_names_with_mutation

    query_dict = {name: (member, query) for name, member, query in decorated_queries}

    query_only = [
        (name, member, query)
        for name, member, query in decorated_queries
        if name not in methods_with_both
    ]
    mutation_only = [
        (name, member)
        for name, member in decorated_mutations
        if name not in methods_with_both
    ]
    both_decorators = [
        (name, query_dict[name][0], query_dict[name][1])
        for name in sorted(methods_with_both)
    ]

    controller_hooks_builder.write(f"export const {pascal_to_camel(class_name)} = {{\n")

    # Hooks for methods with only query decorator
    if query_only:
        controller_hooks_builder.write(f"\t...createClassQueryHooks({class_name},\n")
        for name, member, _ in query_only:
            controller_hooks_builder.write(f'\t\t"{snake_to_camel(name)}",\n')
        controller_hooks_builder.write("\t),\n")

    # Infinite query hooks for query-only methods
    if query_only and any(query.has_infinite_query for _, _, query in query_only):
        controller_hooks_builder.write(
            f"\t...createClassInfiniteQueryHooks({class_name}, {{\n"
        )
        for name, member, query in query_only:
            if query.has_infinite_query:
                controller_hooks_builder.write(
                    f'\t\t"{snake_to_camel(name)}": paginationModelByFirstArgPaginationFilter(),\n'
                )
        controller_hooks_builder.write("\t}),\n")

    # Hooks for methods with only mutation decorator
    if mutation_only:
        controller_hooks_builder.write(f"\t...createClassMutationHooks({class_name},\n")
        for name, member in mutation_only:
            controller_hooks_builder.write(f'\t\t"{snake_to_camel(name)}",\n')
        controller_hooks_builder.write("\t),\n")

    # Namespaced hooks for methods with both decorators
    if both_decorators:
        controller_hooks_builder.write("\tqueries: {\n")
        controller_hooks_builder.write(f"\t\t...createClassQueryHooks({class_name},\n")
        for name, member, query in both_decorators:
            controller_hooks_builder.write(f'\t\t\t"{snake_to_camel(name)}",\n')
        controller_hooks_builder.write("\t\t),\n")
        controller_hooks_builder.write("\t},\n")

        controller_hooks_builder.write("\tmutations: {\n")
        controller_hooks_builder.write(
            f"\t\t...createClassMutationHooks({class_name},\n"
        )
        for name, member, query in both_decorators:
            controller_hooks_builder.write(f'\t\t\t"{snake_to_camel(name)}",\n')
        controller_hooks_builder.write("\t\t),\n")
        controller_hooks_builder.write("\t},\n")

    # Infinite queries namespace for methods with both decorators
    both_with_infinite = [
        (name, member, query)
        for name, member, query in both_decorators
        if query.has_infinite_query
    ]
    if both_with_infinite:
        controller_hooks_builder.write("\tinfiniteQueries: {\n")
        controller_hooks_builder.write(
            f"\t\t...createClassInfiniteQueryHooks({class_name}, {{\n"
        )
        for name, member, query in both_with_infinite:
            controller_hooks_builder.write(
                f'\t\t\t"{snake_to_camel(name)}": paginationModelByFirstArgPaginationFilter(),\n'
            )
        controller_hooks_builder.write("\t\t}),\n")
        controller_hooks_builder.write("\t},\n")

    controller_hooks_builder.write("};\n")

    return controller_hooks_builder
