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


def mount_parameters_arguments(parameters: list[HttpParameterSpec]) -> str:
    return ", ".join(
        [f"{parameter.name}: {parameter.argument_type_str}" for parameter in parameters]
    )


# Backwards compatible aliases for the previous (misspelled) public names
HttpParemeterSpec = HttpParameterSpec
mount_parametes_arguments = mount_parameters_arguments
