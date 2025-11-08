# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from jararaca.observability.constants import TRACEPARENT_KEY


def setup_fastapi_exception_handler(
    app: FastAPI, trace_header_name: str = "traceparent"
) -> None:
    async def base_http_exception_handler(
        request: Request, exc: HTTPException | RequestValidationError
    ) -> JSONResponse | Response:

        if isinstance(exc, RequestValidationError):
            response = await request_validation_exception_handler(request, exc)
            response.headers[trace_header_name] = request.scope.get(TRACEPARENT_KEY, "")
            return response
        else:
            err_response = await http_exception_handler(request, exc)

            err_response.headers[trace_header_name] = request.scope.get(
                TRACEPARENT_KEY, ""
            )
            return err_response

    app.exception_handlers[HTTPException] = base_http_exception_handler
    app.exception_handlers[RequestValidationError] = base_http_exception_handler
