# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator, Literal

# How Python enums are emitted:
# - "enum": TypeScript ``enum`` (default)
# - "const": ``as const`` object plus a type alias with the same name. Much smaller in
#   the final bundle (a TS enum compiles to an IIFE), but ``Color.RED`` can no longer
#   be used as a *type* (use ``typeof Color.RED``).
EnumStyle = Literal["enum", "const"]


class ParseContext:
    def __init__(self, enum_style: EnumStyle = "enum") -> None:
        self.mapped_types: set[Any] = set()
        self.enum_style: EnumStyle = enum_style


# Fallback used when no ``with_parse_context`` block is active (e.g. direct calls to
# the type mapper). A generation run should always use its own context instead.
parse_context_ctxvar = ContextVar("parse_context", default=ParseContext())


@contextmanager
def with_parse_context(
    enum_style: EnumStyle = "enum",
) -> Generator[ParseContext, None, None]:
    """
    Run the block with a fresh ``ParseContext``, restoring the previous one on exit.
    """
    parse_context = ParseContext(enum_style)
    token = parse_context_ctxvar.set(parse_context)
    try:
        yield parse_context
    finally:
        parse_context_ctxvar.reset(token)


def use_parse_context() -> ParseContext:
    return parse_context_ctxvar.get()
