# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import re

CONSTANT_PATTERN = re.compile(r"^[A-Z_]+$")


def is_constant(name: str) -> bool:
    return CONSTANT_PATTERN.match(name) is not None


def snake_to_camel(snake_str: str) -> str:
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def pascal_to_camel(pascal_str: str) -> str:
    """Convert a PascalCase string to camelCase."""
    if not pascal_str:
        return pascal_str
    return pascal_str[0].lower() + pascal_str[1:]
