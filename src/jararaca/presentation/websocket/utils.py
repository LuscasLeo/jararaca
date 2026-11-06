# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Receive helpers for websocket endpoints whose socket is shared with the
WebSocketConnectionManager.

When a push from the manager fails because the client is gone, starlette marks
``application_state`` as DISCONNECTED before the endpoint has read the disconnect.
From then on starlette's ``receive_text``/``receive_bytes``/``receive_json`` raise
``RuntimeError("WebSocket is not connected...")``. These helpers raise
``WebSocketDisconnect(1006)`` in that case instead, so the endpoint's usual
``except WebSocketDisconnect`` path handles it.
"""

from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

ABNORMAL_CLOSURE = 1006


def is_ws_connected(websocket: WebSocket) -> bool:
    """True while both sides of the websocket are still open."""
    return (
        websocket.application_state == WebSocketState.CONNECTED
        and websocket.client_state == WebSocketState.CONNECTED
    )


def _raise_if_dropped(websocket: WebSocket) -> None:
    if (
        websocket.application_state == WebSocketState.DISCONNECTED
        or websocket.client_state == WebSocketState.DISCONNECTED
    ):
        raise WebSocketDisconnect(code=ABNORMAL_CLOSURE)


async def receive_ws_text(websocket: WebSocket) -> str:
    _raise_if_dropped(websocket)
    return await websocket.receive_text()


async def receive_ws_bytes(websocket: WebSocket) -> bytes:
    _raise_if_dropped(websocket)
    return await websocket.receive_bytes()


async def receive_ws_json(websocket: WebSocket, mode: str = "text") -> Any:
    _raise_if_dropped(websocket)
    return await websocket.receive_json(mode=mode)
