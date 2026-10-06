# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import importlib.resources


def read_typescript_prelude() -> str:
    """
    Static header of the generated file: lint pragmas, ``@jararaca/core`` imports
    and the FormData helpers.
    """
    return (
        importlib.resources.files("jararaca.tools.typescript")
        .joinpath("templates/prelude.ts")
        .read_text(encoding="utf-8")
    )
