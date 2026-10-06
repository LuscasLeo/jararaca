# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Emission of a single async method of a generated TypeScript service class.
"""

from io import StringIO

from jararaca.presentation.decorators import HttpMapping, RestController
from jararaca.tools.typescript.http.parameters import (
    HttpParameterSpec,
    mount_parameters_arguments,
    parse_path_with_params,
)
from jararaca.tools.typescript.naming import snake_to_camel


def build_endpoint_path(
    rest_controller: RestController,
    mapping: HttpMapping,
    parameters: list[HttpParameterSpec],
) -> str:
    """
    Join the controller and endpoint paths, converting ``{param}`` into ``:param``
    and avoiding duplicated slashes.
    """
    endpoint_path = parse_path_with_params(mapping.path, parameters)
    controller_path = parse_path_with_params(rest_controller.path or "", parameters)

    path_parts: list[str] = []
    if controller_path and controller_path.strip("/"):
        path_parts.append(controller_path.strip("/"))
    if endpoint_path and endpoint_path.strip("/"):
        path_parts.append(endpoint_path.strip("/"))

    final_path = "/".join(path_parts) if path_parts else ""
    return f"/{final_path}" if final_path else "/"


def _write_named_params(
    buffer: StringIO,
    key: str,
    parameters: list[HttpParameterSpec],
    param_type: str,
) -> None:
    sorted_params = sorted(
        [param for param in parameters if param.type_ == param_type],
        key=lambda x: x.name,
    )
    buffer.write(f"\t\t\t{key}: {{\n")
    for param in sorted_params:
        buffer.write(f'\t\t\t\t"{param.name}": {param.name},\n')
    buffer.write("\t\t\t},\n")


def write_endpoint_method(
    class_buffer: StringIO,
    name: str,
    mapping: HttpMapping,
    rest_controller: RestController,
    parameters: list[HttpParameterSpec],
    return_value_repr: str,
) -> None:
    class_buffer.write(
        f"\tasync {snake_to_camel(name)}({mount_parameters_arguments(parameters)}): Promise<{return_value_repr}> {{\n"
    )

    class_buffer.write("\t\t")
    class_buffer.write(
        "const response = await this.httpBackend.request<%s>({ \n" % return_value_repr
    )
    if mapping.response_type is not None:
        class_buffer.write(f'\t\t\tresponseType: "{mapping.response_type}",\n')
    class_buffer.write(f'\t\t\tmethod: "{mapping.method}",\n')

    formatted_path = build_endpoint_path(rest_controller, mapping, parameters)
    class_buffer.write(f"\t\t\tpath: `{formatted_path}`,\n")

    _write_named_params(class_buffer, "pathParams", parameters, "path")
    _write_named_params(class_buffer, "headers", parameters, "header")
    _write_named_params(class_buffer, "query", parameters, "query")

    # FormData is used for file uploads and form parameters
    form_params = [param for param in parameters if param.type_ == "form"]
    body_param = next((x for x in parameters if x.type_ == "body"), None)

    if form_params:
        class_buffer.write("\t\t\tbody: makeFormData({\n")
        for param in form_params:
            class_buffer.write(f'\t\t\t\t"{param.name}": {param.name},\n')
        # Body parameter may exist alongside form params
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
