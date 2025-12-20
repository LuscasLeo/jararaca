# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later


from typing import TYPE_CHECKING, get_args, get_origin

if TYPE_CHECKING:
    from types import GenericAlias

    from typing_extensions import TypeIs


def is_generic_alias(subject: object) -> "TypeIs[GenericAlias]":
    origin = get_origin(subject)
    args = get_args(subject)

    return origin is not None and len(args) > 0
