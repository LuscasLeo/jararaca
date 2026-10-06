# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator


class ParseContext:
    def __init__(self) -> None:
        self.mapped_types: set[Any] = set()


# Fallback used when no ``with_parse_context`` block is active (e.g. direct calls to
# the type mapper). A generation run should always use its own context instead.
parse_context_ctxvar = ContextVar("parse_context", default=ParseContext())


@contextmanager
def with_parse_context() -> Generator[ParseContext, None, None]:
    """
    Run the block with a fresh ``ParseContext``, restoring the previous one on exit.
    """
    parse_context = ParseContext()
    token = parse_context_ctxvar.set(parse_context)
    try:
        yield parse_context
    finally:
        parse_context_ctxvar.reset(token)


def use_parse_context() -> ParseContext:
    return parse_context_ctxvar.get()
