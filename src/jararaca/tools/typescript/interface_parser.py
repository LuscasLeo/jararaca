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
from fastapi.params import Body, Cookie, Depends, Header, Path, Query
from fastapi.security.http import HTTPBase
from pydantic import BaseModel, PlainValidator
from pydantic_core import PydanticUndefinedType

from jararaca.microservice import Microservice
from jararaca.presentation.decorators import HttpMapping, RestController
from jararaca.presentation.websocket.decorators import RegisterWebSocketMessage
from jararaca.presentation.websocket.websocket_interceptor import (
    WebSocketMessageWrapper,
)
from jararaca.tools.typescript.decorators import MutationEndpoint, QueryEndpoint

CONSTANT_PATTERN = re.compile(r"^[A-Z_]+$")


def is_constant(name: str) -> bool:
    return CONSTANT_PATTERN.match(name) is not None


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


def get_field_type_for_ts(field_type: Any) -> Any:
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
        return get_field_type_for_ts(field_type.__args__[0])
    if get_origin(field_type) == tuple:
        return f"[{', '.join([get_field_type_for_ts(field) for field in field_type.__args__])}]"
    if get_origin(field_type) == list or get_origin(field_type) == frozenset:
        return f"Array<{get_field_type_for_ts(field_type.__args__[0])}>"
    if get_origin(field_type) == set:
        return f"Array<{get_field_type_for_ts(field_type.__args__[0])}> // Set"
    if get_origin(field_type) == dict:
        return f"{{[key: {get_field_type_for_ts(field_type.__args__[0])}]: {get_field_type_for_ts(field_type.__args__[1])}}}"
    if inspect.isclass(field_type):
        if not hasattr(field_type, "__pydantic_generic_metadata__"):
            return field_type.__name__
        pydantic_metadata = getattr(field_type, "__pydantic_generic_metadata__")

        name = (
            pydantic_metadata.get("origin").__name__
            if pydantic_metadata.get("origin") is not None
            else field_type.__name__
        )
        args = pydantic_metadata.get("args")

        if len(args) > 0:
            return "%s<%s>" % (
                name,
                ", ".join([get_field_type_for_ts(arg) for arg in args]),
            )

        return name

    if hasattr(field_type, "__class__") and field_type.__class__ is TypeVar:
        return field_type.__name__
    if get_origin(field_type) == Literal:
        return " | ".join([parse_literal_value(x) for x in field_type.__args__])
    if get_origin(field_type) == UnionType or get_origin(field_type) == typing.Union:
        return " | ".join([get_field_type_for_ts(x) for x in field_type.__args__])
    if (get_origin(field_type) == Annotated) and (len(field_type.__args__) > 0):
        if (
            plain_validator := next(
                (x for x in field_type.__metadata__ if isinstance(x, PlainValidator)),
                None,
            )
        ) is not None:
            return get_field_type_for_ts(plain_validator.json_schema_input_type)
        return get_field_type_for_ts(field_type.__args__[0])
    return "unknown"


def is_generic_type(field_type: Any) -> bool:
    return hasattr(field_type, "__parameters__") and len(field_type.__parameters__) > 0


def get_generic_args(field_type: Any) -> Any:
    return field_type.__parameters__


def parse_type_to_typescript_interface(
    basemodel_type: Type[Any],
) -> tuple[set[type], str]:
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
            f"export enum {basemodel_type.__name__} {{\n"
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

    extends_expression = (
        " extends %s"
        % ", ".join(
            sorted(
                [
                    (
                        "%s" % get_field_type_for_ts(inherited_class)
                        if not inherited_classes_consts_conflict[inherited_class]
                        else "Omit<%s, %s>"
                        % (
                            get_field_type_for_ts(inherited_class),
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
                    )
                    for inherited_class in valid_inherited_classes
                ],
                key=lambda x: str(x),
            )
        )
        if len(valid_inherited_classes) > 0
        else ""
    )

    if is_generic_type(basemodel_type):
        generic_args = get_generic_args(basemodel_type)
        string_builder.write(
            f"export interface {basemodel_type.__name__}<{', '.join(sorted([arg.__name__ for arg in generic_args]))}>{extends_expression} {{\n"
        )
    else:
        string_builder.write(
            f"export interface {basemodel_type.__name__}{extends_expression} {{\n"
        )

    if hasattr(basemodel_type, "__annotations__"):
        # Sort fields for consistent output
        annotation_items = sorted(
            basemodel_type.__annotations__.items(), key=lambda x: x[0]
        )

        for field_name, field in annotation_items:
            if field_name in cls_consts:
                continue

            has_default_value = (
                get_origin(field) is not Literal
                and field_name in basemodel_type.model_fields
                and not isinstance(
                    basemodel_type.model_fields[field_name].default,
                    PydanticUndefinedType,
                )
            )
            string_builder.write(
                f"  {snake_to_camel(field_name) if not is_constant(field_name) else field_name}{'?' if has_default_value else ''}: {get_field_type_for_ts(field)};\n"
            )
            mapped_types.update(extract_all_envolved_types(field))
            mapped_types.add(field)

    ## Loop over computed fields - sort them for consistent output
    members = sorted(
        inspect.getmembers(basemodel_type, lambda a: isinstance(a, property)),
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
                    f"  {snake_to_camel(field_name)}: {get_field_type_for_ts(return_type)};\n"
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

    for controller in microservice.controllers:
        rest_controller = RestController.get_controller(controller)

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

        registered = RegisterWebSocketMessage.get(controller)

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

import { HttpService, HttpBackend, HttpBackendRequest, ResponseType, createClassQueryHooks , createClassMutationHooks, createClassInfiniteQueryHooks, paginationModelByFirstArgPaginationFilter } from "@jararaca/core";
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

    # Sort members for consistent output
    member_items = sorted(
        inspect.getmembers(controller, predicate=inspect.isfunction), key=lambda x: x[0]
    )

    for name, member in member_items:
        if (mapping := HttpMapping.get_http_mapping(member)) is not None:
            return_type = member.__annotations__.get("return")

            if return_type is None:
                return_type = NoneType

            if query_endpoint := QueryEndpoint.extract_query_endpoint(member):
                decorated_queries.append((name, member, query_endpoint))
            if MutationEndpoint.is_mutation(member):
                decorated_mutations.append((name, member))

            mapped_types.update(extract_all_envolved_types(return_type))

            return_value_repr = get_field_type_for_ts(return_type)

            arg_params_spec, parametes_mapped_types = extract_parameters(
                member, rest_controller, mapping
            )

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
            final_path = "/".join(
                s.strip("/") for s in [rest_controller.path, endpoint_path]
            )
            class_buffer.write(f"\t\t\tpath: `/{final_path}`,\n")

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

            if (
                body := next((x for x in arg_params_spec if x.type_ == "body"), None)
            ) is not None:
                class_buffer.write(f"\t\t\tbody: {body.name}\n")
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
    type_: Literal["query", "path", "body", "header", "cookie"]
    name: str
    required: bool
    argument_type_str: str


def parse_path_with_params(path: str, parameters: list[HttpParemeterSpec]) -> str:
    # Use a regular expression to match both simple parameters {param} and
    # parameters with converters {param:converter}
    import re

    pattern = re.compile(r"{([^:}]+)(?::[^}]*)?}")

    # For each parameter found in the path, replace it with :param format
    for parameter in parameters:
        path = pattern.sub(
            lambda m: f":{m.group(1)}" if m.group(1) == parameter.name else m.group(0),
            path,
        )

    return path


def mount_parametes_arguments(parameters: list[HttpParemeterSpec]) -> str:
    return ", ".join(
        [f"{parameter.name}: {parameter.argument_type_str}" for parameter in parameters]
    )


def extract_parameters(
    member: Any, controller: RestController, mapping: HttpMapping
) -> tuple[list[HttpParemeterSpec], set[Any]]:
    parameters_list: list[HttpParemeterSpec] = []
    mapped_types: set[Any] = set()
    if get_origin(member) is UnionType:
        for arg in member.__args__:
            if is_primitive(arg):
                continue
            rec_parameters, rec_mapped_types = extract_parameters(
                arg, controller, mapping
            )
            mapped_types.update(rec_mapped_types)
            parameters_list.extend(rec_parameters)
        return parameters_list, mapped_types

    if is_primitive(member):

        if get_origin(member) is Annotated:
            if (
                plain_validator := next(
                    (x for x in member.__metadata__ if isinstance(x, PlainValidator)),
                    None,
                )
            ) is not None:
                mapped_types.add(plain_validator.json_schema_input_type)
                return parameters_list, mapped_types
            return extract_parameters(member.__args__[0], controller, mapping)
        return parameters_list, mapped_types

    if hasattr(member, "__bases__"):
        for base in member.__bases__:
            # if base is not BaseModel:
            rec_parameters, rec_mapped_types = extract_parameters(
                base, controller, mapping
            )
            mapped_types.update(rec_mapped_types)
            parameters_list.extend(rec_parameters)

    if hasattr(member, "__annotations__"):
        for parameter_name, parameter_type in member.__annotations__.items():
            if parameter_name == "return":
                continue
            if parameter_type in EXCLUDED_REQUESTS_TYPES:
                continue

            if get_origin(parameter_type) == Annotated:
                annotated_type_hook = parameter_type.__metadata__[0]
                annotated_type = parameter_type.__args__[0]
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
                elif isinstance(annotated_type_hook, Body):
                    mapped_types.update(extract_all_envolved_types(parameter_type))
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="body",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(parameter_type),
                        )
                    )
                elif isinstance(annotated_type_hook, Query):
                    mapped_types.add(parameter_type)
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="query",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(parameter_type),
                        )
                    )
                elif isinstance(annotated_type_hook, Path):
                    mapped_types.add(parameter_type)
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="path",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(parameter_type),
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
                            depends_hook, controller, mapping
                        )
                        mapped_types.update(rec_mapped_types)
                        parameters_list.extend(rec_parameters)
                elif (
                    re.search(f":{parameter_name}(?:/|$)", controller.path) is not None
                ):
                    mapped_types.add(annotated_type)
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="path",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(annotated_type),
                        )
                    )
                else:
                    mapped_types.add(annotated_type)
                    parameters_list.append(
                        HttpParemeterSpec(
                            type_="query",
                            name=parameter_name,
                            required=True,
                            argument_type_str=get_field_type_for_ts(annotated_type),
                        )
                    )

            elif inspect.isclass(parameter_type) and issubclass(
                parameter_type, BaseModel
            ):
                mapped_types.update(extract_all_envolved_types(parameter_type))
                parameters_list.append(
                    HttpParemeterSpec(
                        type_="body",
                        name=parameter_name,
                        required=True,
                        argument_type_str=get_field_type_for_ts(parameter_type),
                    )
                )
            elif (
                # Match both simple parameters {param} and parameters with converters {param:converter}
                re.search(f"{{{parameter_name}(:.*?)?}}", controller.path) is not None
                or re.search(f"{{{parameter_name}(:.*?)?}}", mapping.path) is not None
            ):
                mapped_types.add(parameter_type)
                parameters_list.append(
                    HttpParemeterSpec(
                        type_="path",
                        name=parameter_name,
                        required=True,
                        argument_type_str=get_field_type_for_ts(parameter_type),
                    )
                )
            else:
                mapped_types.add(parameter_type)
                parameters_list.append(
                    HttpParemeterSpec(
                        type_="query",
                        name=parameter_name,
                        required=True,
                        argument_type_str=get_field_type_for_ts(parameter_type),
                    )
                )

            if inspect.isclass(parameter_type) and not is_primitive(parameter_type):
                signature = inspect.signature(parameter_type)

                parameter_members = signature.parameters

                for _, parameter_type in parameter_members.items():
                    if is_primitive(parameter_type.annotation):
                        if get_origin(parameter_type.annotation) is not None:
                            if (
                                get_origin(parameter_type.annotation) == Annotated
                                and (
                                    plain_validator := next(
                                        (
                                            x
                                            for x in parameter_type.annotation.__metadata__
                                            if isinstance(x, PlainValidator)
                                        ),
                                        None,
                                    )
                                )
                                is not None
                            ):
                                mapped_types.add(plain_validator.json_schema_input_type)
                            else:
                                args = parameter_type.annotation.__args__
                                mapped_types.update(args)
                        else:
                            continue
                    _, types = extract_parameters(
                        parameter_type.annotation, controller, mapping
                    )
                    mapped_types.update(types)

    if hasattr(member, "__args__"):
        for arg in member.__args__:

            rec_parameters, rec_mapped_types = extract_parameters(
                arg, controller, mapping
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
            if (
                get_origin(field_type) == Annotated
                and (
                    plain_validator := next(
                        (
                            x
                            for x in field_type.__metadata__
                            if isinstance(x, PlainValidator)
                        ),
                        None,
                    )
                )
                is not None
            ):
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
