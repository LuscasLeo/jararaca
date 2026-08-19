# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
from typing import Any, Callable, cast

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from jararaca.observability.constants import TRACEPARENT_KEY

ExceptionHandler = Callable[..., Any]


def resolve_traceparent(request: Request) -> str:
    """
    W3C ``traceparent`` of the root span serving *request*, or an empty string.

    The value is normally published into the ASGI scope by the observability
    interceptor. The current span is consulted as a fallback so that failures raised
    before the interceptor runs (in user middleware, for instance) still get a header
    whenever a span exists at all.
    """
    scope_value = request.scope.get(TRACEPARENT_KEY)

    if isinstance(scope_value, str) and scope_value:
        return scope_value

    try:
        from opentelemetry import trace

        from jararaca.observability.providers.otel import format_traceparent
    except ImportError:
        return ""

    return format_traceparent(trace.get_current_span().get_span_context())


def apply_traceparent_header(
    request: Request, response: Response, trace_header_name: str
) -> Response:
    """Stamp *response* with the trace header, unless there is no trace to point at."""
    traceparent = resolve_traceparent(request)

    if traceparent:
        response.headers[trace_header_name] = traceparent

    return response


async def _call_exception_handler(
    handler: ExceptionHandler, request: Request, exc: Exception
) -> Response:
    """Invoke an already installed handler the same way Starlette would."""
    if inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(
        getattr(handler, "__call__", None)
    ):
        return cast(Response, await handler(request, exc))

    return cast(Response, await run_in_threadpool(handler, request, exc))


def setup_fastapi_exception_handler(
    app: FastAPI, trace_header_name: str = "traceparent"
) -> None:
    """
    Make every error response carry the trace header of the request that produced it.

    Covers handled failures (``HTTPException``, request validation) and unhandled ones
    that bubble up to a 500. The 500 handler is what Starlette's ``ServerErrorMiddleware``
    calls; any handler the application already registered for ``Exception``/``500`` is
    wrapped rather than replaced, and the exception keeps propagating afterwards so the
    server still logs it.
    """

    async def base_http_exception_handler(
        request: Request, exc: HTTPException | RequestValidationError
    ) -> Response:

        response: Response
        if isinstance(exc, RequestValidationError):
            response = await request_validation_exception_handler(request, exc)
        else:
            response = await http_exception_handler(request, exc)

        return apply_traceparent_header(request, response, trace_header_name)

    existing_server_error_handler: ExceptionHandler | None = app.exception_handlers.get(
        Exception
    ) or app.exception_handlers.get(500)

    async def server_error_handler(request: Request, exc: Exception) -> Response:
        if existing_server_error_handler is not None:
            response = await _call_exception_handler(
                existing_server_error_handler, request, exc
            )
        else:
            # Mirrors Starlette's own default 500 body so installing this handler does
            # not change what clients receive, only the headers.
            response = PlainTextResponse("Internal Server Error", status_code=500)

        return apply_traceparent_header(request, response, trace_header_name)

    app.exception_handlers[HTTPException] = base_http_exception_handler
    app.exception_handlers[RequestValidationError] = base_http_exception_handler
    app.exception_handlers[Exception] = server_error_handler
    app.exception_handlers.pop(500, None)
