# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from fastapi import Request, Response, WebSocket


class PresentationException(Exception):
    """Base exception for presentation layer errors."""

    def __init__(
        self,
        *,
        original_exception: Exception,
        request: Request | None = None,
        response: Response | None = None,
        websocket: WebSocket | None = None,
    ) -> None:
        super().__init__(str(original_exception))
        self.original_exception = original_exception
        self.request = request
        self.response = response
        self.websocket = websocket
