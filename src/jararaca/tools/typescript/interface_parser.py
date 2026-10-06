# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Public facade of the TypeScript generation pipeline.

The implementation lives in the sibling modules, split by responsibility:

- ``naming``: identifier case conversion
- ``type_introspection``: inspection of Python/Pydantic types
- ``parse_context``: shared state used while parsing types
- ``type_mapper``: Python type -> TypeScript type expression
- ``model_interfaces``: Pydantic models -> TypeScript interfaces/enums
- ``http``: REST controllers -> TypeScript service classes and hooks
- ``microservice_writer``: orchestration of the whole output
"""

from jararaca.tools.typescript.http.controller_writer import (
    write_rest_controller_to_typescript_interface,
)
from jararaca.tools.typescript.http.middleware_extractor import (
    extract_middleware_parameters,
)
from jararaca.tools.typescript.http.parameter_extractor import extract_parameters
from jararaca.tools.typescript.http.parameters import (
    EXCLUDED_REQUESTS_TYPES,
    HttpParameterSpec,
    HttpParemeterSpec,
    mount_parameters_arguments,
    mount_parametes_arguments,
    parse_path_with_params,
)
from jararaca.tools.typescript.microservice_writer import (
    write_microservice_to_typescript_interface,
)
from jararaca.tools.typescript.model_interfaces import (
    parse_single_typescript_interface,
    parse_split_input_output_interfaces,
    parse_type_to_typescript_interface,
)
from jararaca.tools.typescript.naming import (
    CONSTANT_PATTERN,
    is_constant,
    pascal_to_camel,
    snake_to_camel,
)
from jararaca.tools.typescript.parse_context import (
    EnumStyle,
    ParseContext,
    parse_context_ctxvar,
    use_parse_context,
    with_parse_context,
)
from jararaca.tools.typescript.type_introspection import (
    extract_all_envolved_types,
    get_generic_args,
    get_inherited_classes,
    has_default_value,
    is_generic_type,
    is_primitive,
    is_upload_file_type,
    should_exclude_field,
    unwrap_annotated_type,
)
from jararaca.tools.typescript.type_mapper import (
    get_field_type_for_ts,
    get_generic_type_mapping,
    parse_literal_value,
)

__all__ = [
    "CONSTANT_PATTERN",
    "EnumStyle",
    "EXCLUDED_REQUESTS_TYPES",
    "HttpParameterSpec",
    "HttpParemeterSpec",
    "ParseContext",
    "extract_all_envolved_types",
    "extract_middleware_parameters",
    "extract_parameters",
    "get_field_type_for_ts",
    "get_generic_args",
    "get_generic_type_mapping",
    "get_inherited_classes",
    "has_default_value",
    "is_constant",
    "is_generic_type",
    "is_primitive",
    "is_upload_file_type",
    "mount_parameters_arguments",
    "mount_parametes_arguments",
    "parse_context_ctxvar",
    "parse_literal_value",
    "parse_path_with_params",
    "parse_single_typescript_interface",
    "parse_split_input_output_interfaces",
    "parse_type_to_typescript_interface",
    "pascal_to_camel",
    "should_exclude_field",
    "snake_to_camel",
    "unwrap_annotated_type",
    "use_parse_context",
    "with_parse_context",
    "write_microservice_to_typescript_interface",
    "write_rest_controller_to_typescript_interface",
]
