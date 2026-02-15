# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator

FieldValue = bool | float | int | None | str

ImplicitHeaders = dict[str, FieldValue]

implicit_headers_ctx = ContextVar[ImplicitHeaders]("implicit_headers_ctx", default={})


def use_implicit_headers() -> ImplicitHeaders:
    return implicit_headers_ctx.get()


@contextmanager
def provide_implicit_headers(
    implicit_headers: ImplicitHeaders,
    *,
    reset: bool = False,
) -> Generator[None, Any, None]:
    previous_implicit_headers = implicit_headers_ctx.get() if not reset else {}
    new_implicit_headers = previous_implicit_headers | implicit_headers
    token = implicit_headers_ctx.set(new_implicit_headers)
    try:
        yield
    finally:
        implicit_headers_ctx.reset(token)
