# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import re
from dataclasses import dataclass
from typing import Literal

from fastapi import Request, Response

EXCLUDED_REQUESTS_TYPES = [Request, Response]


@dataclass
class HttpParameterSpec:
    type_: Literal["query", "path", "body", "header", "cookie", "form"]
    name: str
    required: bool
    argument_type_str: str


def parse_path_with_params(path: str, parameters: list[HttpParameterSpec]) -> str:
    # Use a regular expression to match both simple parameters {param} and
    # parameters with converters {param:converter}
    pattern = re.compile(r"{([^:}]+)(?::[^}]*)?}")

    # For each parameter found in the path, replace it with :param format
    for parameter in parameters:
        path = pattern.sub(
            lambda m: f":{m.group(1)}" if m.group(1) == parameter.name else m.group(0),
            path,
        )

    return path


class ConflictingParametersError(ValueError):
    """Two parameters share a name but disagree on where they go or on their type."""


def merge_duplicate_parameters(
    parameters: list[HttpParameterSpec], owner: str
) -> list[HttpParameterSpec]:
    """
    Collapse parameters that share a name (e.g. the same header required by a
    middleware and by the handler), keeping the first occurrence.

    The generated method signature and the ``pathParams``/``headers``/``query``
    objects can only have one entry per name; a repeated name is a syntax error in
    ES modules. Parameters with the same name but a different kind or TypeScript
    type cannot be merged safely, so they raise instead of guessing.
    """
    merged: dict[str, HttpParameterSpec] = {}
    for parameter in parameters:
        existing = merged.get(parameter.name)
        if existing is None:
            merged[parameter.name] = parameter
        elif (
            existing.type_ != parameter.type_
            or existing.argument_type_str != parameter.argument_type_str
        ):
            raise ConflictingParametersError(
                f"{owner}: parameter '{parameter.name}' is declared more than once "
                f"with different definitions "
                f"({existing.type_}: {existing.argument_type_str} vs "
                f"{parameter.type_}: {parameter.argument_type_str})"
            )
    return list(merged.values())


def mount_parameters_arguments(parameters: list[HttpParameterSpec]) -> str:
    return ", ".join(
        [f"{parameter.name}: {parameter.argument_type_str}" for parameter in parameters]
    )


# Backwards compatible aliases for the previous (misspelled) public names
HttpParemeterSpec = HttpParameterSpec
mount_parametes_arguments = mount_parameters_arguments
