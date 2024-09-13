import inspect
import typing
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from io import StringIO
from types import NoneType, UnionType
from typing import Annotated, Any, Generic, Literal, Type, TypeVar, get_origin
from uuid import UUID

from fastapi import Request, Response
from fastapi.params import Body, Cookie, Depends, Header, Path, Query
from fastapi.security.http import HTTPBase
from pydantic import BaseModel

from jararaca.microservice import Microservice
from jararaca.presentation.decorators import HttpMapping, RestController


def snake_to_camel(snake_str: str) -> str:
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def get_field_type_for_ts(field_type: Any) -> Any:
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
    if get_origin(field_type) == tuple:
        return f"[{', '.join([get_field_type_for_ts(field) for field in field_type.__args__])}]"
    if get_origin(field_type) == list:
        return f"Array<{get_field_type_for_ts(field_type.__args__[0])}>"
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
        return " | ".join([f'"{x}"' for x in field_type.__args__])
    if get_origin(field_type) == UnionType or get_origin(field_type) == typing.Union:
        return " | ".join([get_field_type_for_ts(x) for x in field_type.__args__])
    if (get_origin(field_type) == Annotated) and (len(field_type.__args__) > 0):
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

    mapped_types.update(inherited_classes)

    if Enum in inherited_classes:
        return (
            set(),
            f"export enum {basemodel_type.__name__} {{\n"
            + "\n ".join([f'\t{x.value} = "{x.value}",' for x in basemodel_type])
            + "\n}\n",
        )

    extends_expression = (
        f" extends {', '.join([get_field_type_for_ts(inherited_class) for inherited_class in inherited_classes])}"
        if len(inherited_classes) > 0
        else ""
    )

    if is_generic_type(basemodel_type):
        string_builder.write(
            f"export interface {basemodel_type.__name__}<{', '.join([arg.__name__ for arg in get_generic_args(basemodel_type)])}>{extends_expression} {{\n"
        )
    else:
        string_builder.write(
            f"export interface {basemodel_type.__name__}{extends_expression} {{\n"
        )

    for field_name, field in basemodel_type.__annotations__.items():
        string_builder.write(
            f"  {snake_to_camel(field_name)}: {get_field_type_for_ts(field)};\n"
        )

    string_builder.write("}\n")

    return mapped_types, string_builder.getvalue()


def get_inherited_classes(basemodel_type: Type[Any]) -> list[Type[Any]]:
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

    for controller in microservice.controllers:
        rest_controller = RestController.get_controller(controller)
        if rest_controller is None:
            continue

        controller_class_str, types = write_rest_controller_to_typescript_interface(
            rest_controller, controller
        )

        mapped_types_set.update(types)
        rest_controller_buffer.write(controller_class_str)

    final_buffer = StringIO()

    final_buffer.write(
        """
export interface HttpBackendRequest {
  method: string;
  path: string;
  headers: { [key: string]: string };
  query: { [key: string]: string };
  body: any;
}

export interface HttpBackend {
  request<T>(request: HttpBackendRequest): Promise<T>;
}

export abstract class HttpService {
  constructor(protected readonly httpBackend: HttpBackend) {}
}
"""
    )

    processed_types: set[Any] = set()
    backlog: set[Any] = mapped_types_set.copy()

    while len(backlog) > 0:
        t = backlog.pop()
        if not is_primitive(t):
            new_types, text = parse_type_to_typescript_interface(t)
            final_buffer.write(text)

            processed_types.add(t)
            backlog.update(new_types)

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
        ]
        or get_origin(field_type) in [list, dict, tuple, Literal, UnionType]
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
            ),
        )
    )


def write_rest_controller_to_typescript_interface(
    rest_controller: RestController, controller: type
) -> tuple[str, set[Any]]:

    class_buffer = StringIO()

    class_buffer.write(f"export class {controller.__name__} extends HttpService {{\n")

    mapped_types: set[Any] = set()

    for name, member in inspect.getmembers(controller, predicate=inspect.isfunction):
        if (mapping := HttpMapping.get_http_mapping(member)) is not None:
            return_type = member.__annotations__.get("return")

            if return_type is None:
                return_type = NoneType

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
            class_buffer.write(f'\t\t\tmethod: "{mapping.method}",\n')

            endpoint_path = parse_path_with_params(mapping.path, arg_params_spec)
            final_path = "/".join(
                s.strip("/") for s in [rest_controller.path, endpoint_path]
            )
            class_buffer.write(f"\t\t\tpath: `/{final_path}`,\n")

            class_buffer.write("\t\t\theaders: {\n")
            for param in arg_params_spec:
                if param.type_ == "header":
                    class_buffer.write(
                        f'\t\t\t\t"{param.name}": String({param.name}),\n'
                    )

            class_buffer.write("\t\t\t},\n")
            class_buffer.write("\t\t\tquery: {\n")

            for param in arg_params_spec:
                if param.type_ == "query":
                    class_buffer.write(
                        f'\t\t\t\t"{param.name}": String({param.name}),\n'
                    )
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

    return class_buffer.getvalue(), mapped_types


EXCLUDED_REQUESTS_TYPES = [Request, Response]


@dataclass
class HttpParemeterSpec:
    type_: Literal["query", "path", "body", "header", "cookie"]
    name: str
    required: bool
    argument_type_str: str


def parse_path_with_params(path: str, parameters: list[HttpParemeterSpec]) -> str:
    for parameter in parameters:
        path = path.replace(f"{{{parameter.name}}}", f"${{{parameter.name}}}")
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
        return parameters_list, mapped_types

    if get_origin(member) is Annotated:
        return extract_parameters(member.__args__[0], controller, mapping)

    if hasattr(member, "__bases__"):
        for base in member.__bases__:
            # if base is not BaseModel:
            rec_parameters, rec_mapped_types = extract_parameters(
                base, controller, mapping
            )
            mapped_types.update(rec_mapped_types)
            parameters_list.extend(rec_parameters)

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
                depends_hook = annotated_type_hook.dependency

                if isinstance(depends_hook, HTTPBase):
                    ...

                else:
                    rec_parameters, rec_mapped_types = extract_parameters(
                        depends_hook, controller, mapping
                    )
                    mapped_types.update(rec_mapped_types)
                    parameters_list.extend(rec_parameters)
            elif controller.path.find(f":{parameter_name}") != -1:
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

        elif inspect.isclass(parameter_type) and issubclass(parameter_type, BaseModel):
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
            controller.path.find(f"{{{parameter_name}}}") != -1
            or mapping.path.find(f"{{{parameter_name}}}") != -1
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
                        args = parameter_type.annotation.__args__
                        mapped_types.update(args)
                    else:
                        continue
                _, types = extract_parameters(
                    parameter_type.annotation, controller, mapping
                )
                mapped_types.update(types)

    return parameters_list, mapped_types


def extract_all_envolved_types(field_type: Any) -> set[Any]:
    mapped_types: set[Any] = set()

    if field_type is None:
        return mapped_types

    if is_primitive(field_type):
        if get_origin(field_type) is not None:
            mapped_types.update(field_type.__args__)

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


class TableType(str, Enum):
    PORTABILIDADE = "PORTABILIDADE"
    PORT_MAIS_REFINANCIAMENTO = "PORT_MAIS_REFINANCIAMENTO"


@RestController("/test")
class TestController:
    @HttpMapping(method="GET", path="/test/{id}")
    def get_test(self, test: TableType) -> TableType:
        raise NotImplementedError()
