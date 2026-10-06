# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator


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
