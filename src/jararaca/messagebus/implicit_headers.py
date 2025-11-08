# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import datetime
import decimal
import typing
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Generator

FieldArray = list["FieldValue"]
"""A data structure for holding an array of field values."""

FieldTable = typing.Dict[str, "FieldValue"]
FieldValue = (
    bool
    | bytes
    | bytearray
    | decimal.Decimal
    | FieldArray
    | FieldTable
    | float
    | int
    | None
    | str
    | datetime.datetime
)

ImplicitHeaders = Dict[str, FieldValue]

implicit_headers_ctx = ContextVar[ImplicitHeaders | None](
    "implicit_headers_ctx", default=None
)


def use_implicit_headers() -> ImplicitHeaders | None:
    return implicit_headers_ctx.get()


@contextmanager
def provide_implicit_headers(
    implicit_headers: ImplicitHeaders,
) -> Generator[None, Any, None]:
    token = implicit_headers_ctx.set(implicit_headers)
    try:
        yield
    finally:
        implicit_headers_ctx.reset(token)
