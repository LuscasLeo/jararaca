# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Building blocks shared by the handler and middleware parameter extractors.
"""

import inspect
from typing import Annotated, Any, Literal, get_origin

from fastapi.params import Body, Cookie, Depends, Form, Header, Path, Query

from jararaca.tools.typescript.decorators import SplitInputOutput
from jararaca.tools.typescript.http.parameters import HttpParameterSpec
from jararaca.tools.typescript.type_introspection import (
    extract_all_envolved_types,
    is_upload_file_type,
    unwrap_annotated_type,
)
from jararaca.tools.typescript.type_mapper import get_field_type_for_ts

ParameterKind = Literal["query", "path", "body", "header", "cookie", "form"]

FASTAPI_PARAMETER_MARKERS = (Header, Cookie, Form, Body, Query, Path, Depends)


def input_suffix(python_type: Any) -> str:
    """
    ``"Input"`` when the type is a ``@SplitInputOutput`` model, so the request side
    references the Input interface instead of the Output one.
    """
    if (
        inspect.isclass(python_type)
        and hasattr(python_type, "__dict__")
        and SplitInputOutput.is_split_model(python_type)
    ):
        return "Input"
    return ""


def build_parameter_spec(
    kind: ParameterKind,
    name: str,
    python_type: Any,
    type_mapping: dict[Any, Any] | None = None,
    *,
    split_aware: bool = True,
) -> HttpParameterSpec:
    """
    Build the spec of a parameter, mapping its Python type to a TypeScript one.
    """
    return HttpParameterSpec(
        type_=kind,
        name=name,
        required=True,
        argument_type_str=get_field_type_for_ts(
            python_type,
            input_suffix(python_type) if split_aware else "",
            type_mapping,
        ),
    )


def resolve_annotated_marker(parameter_type: Any) -> tuple[Any, Any]:
    """
    Split an ``Annotated`` type into its marker and the underlying type.

    The marker is the first FastAPI parameter annotation found in the metadata,
    falling back to the first metadata entry (or ``None``).
    """
    unwrapped_type, all_metadata = unwrap_annotated_type(parameter_type)
    marker = next(
        (m for m in all_metadata if isinstance(m, FASTAPI_PARAMETER_MARKERS)), None
    )
    if marker is None and all_metadata:
        marker = all_metadata[0]
    return marker, unwrapped_type


def is_annotated(parameter_type: Any) -> bool:
    return bool(get_origin(parameter_type) == Annotated)


def build_marked_parameter(
    marker: Any,
    name: str,
    parameter_type: Any,
    annotated_type: Any,
    type_mapping: dict[Any, Any] | None = None,
) -> tuple[HttpParameterSpec, set[Any]] | None:
    """
    Build the parameter declared with an explicit FastAPI marker (``Header()``,
    ``Cookie()``, ``Form()``, ``Body()``, ``Query()`` or ``Path()``).

    Returns the spec and the types it references, or ``None`` for any other marker.
    """
    if isinstance(marker, Header):
        return (
            build_parameter_spec("header", name, str, type_mapping, split_aware=False),
            {str},
        )
    if isinstance(marker, Cookie):
        return (
            build_parameter_spec("cookie", name, str, type_mapping, split_aware=False),
            {str},
        )
    if isinstance(marker, Form):
        return (
            build_parameter_spec(
                "form", name, annotated_type, type_mapping, split_aware=False
            ),
            {annotated_type},
        )
    if isinstance(marker, Body):
        return (
            build_parameter_spec("body", name, parameter_type, type_mapping),
            extract_all_envolved_types(parameter_type),
        )
    if isinstance(marker, Query):
        return (
            build_parameter_spec("query", name, parameter_type, type_mapping),
            {parameter_type},
        )
    if isinstance(marker, Path):
        return (
            build_parameter_spec("path", name, parameter_type, type_mapping),
            {parameter_type},
        )
    return None


def build_unmarked_parameter(
    name: str,
    python_type: Any,
    type_mapping: dict[Any, Any] | None,
    *,
    is_path: bool,
) -> HttpParameterSpec:
    """
    Build a parameter that carries no explicit FastAPI marker: path parameter when
    it appears in the route, form data for uploads, query parameter otherwise.
    """
    if is_path:
        return build_parameter_spec("path", name, python_type, type_mapping)
    if is_upload_file_type(python_type):
        return build_parameter_spec(
            "form", name, python_type, type_mapping, split_aware=False
        )
    return build_parameter_spec("query", name, python_type, type_mapping)
