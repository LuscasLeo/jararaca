# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Regression tests for WebSocketConnectionManagerImpl when a client drops before the
endpoint has read the disconnect.

Sequence seen in production: the endpoint accepts, the client goes away, a backend
push reaches the manager while ``client_state`` is still CONNECTED, ``send_bytes``
fails with an OSError (uvicorn's ``ClientDisconnected``) and starlette flips
``application_state`` to DISCONNECTED. The endpoint's next ``receive_text()`` then
raised ``RuntimeError: WebSocket is not connected`` instead of a disconnect.
"""

import asyncio
from typing import Any

import pytest
from starlette.types import Message
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from jararaca.presentation.websocket.utils import (
    is_ws_connected,
    receive_ws_bytes,
    receive_ws_json,
    receive_ws_text,
)
from jararaca.presentation.websocket.websocket_interceptor import (
    BroadcastFunc,
    SendFunc,
    WebSocketConnectionManagerImpl,
)


class ClientGone(OSError):
    """Stands in for uvicorn's ClientDisconnected (an OSError subclass)."""


class NullBackend:
    def configure(
        self, broadcast: BroadcastFunc, send: SendFunc, shutdown_event: asyncio.Event
    ) -> None:
        pass

    async def broadcast(self, message: bytes) -> None:
        pass

    async def send(self, rooms: list[str], message: bytes) -> None:
        pass

    async def shutdown(self) -> None:
        pass


class FakeConnection:
    """ASGI side of one websocket; ``dead`` makes every send fail like uvicorn does."""

    def __init__(self) -> None:
        self.inbox: list[Message] = [{"type": "websocket.connect"}]
        self.dead = False
        self.sent: list[Message] = []

    async def receive(self) -> Message:
        if self.inbox:
            return self.inbox.pop(0)
        return {"type": "websocket.disconnect", "code": 1006}

    async def send(self, message: Message) -> None:
        if self.dead and message["type"] == "websocket.send":
            raise ClientGone()
        self.sent.append(message)

    def data_sent(self) -> list[Any]:
        return [m.get("bytes") for m in self.sent if m["type"] == "websocket.send"]


async def accepted_ws() -> tuple[WebSocket, FakeConnection]:
    conn = FakeConnection()
    scope = {"type": "websocket", "path": "/ws", "headers": [], "query_string": b""}
    ws = WebSocket(scope, conn.receive, conn.send)
    await ws.accept()
    return ws, conn


def make_manager() -> WebSocketConnectionManagerImpl:
    return WebSocketConnectionManagerImpl(NullBackend(), asyncio.Event())


async def test_room_push_to_dropped_client_surfaces_as_disconnect() -> None:
    ws, conn = await accepted_ws()
    mgr = make_manager()
    await mgr.add_websocket(ws)
    await mgr.join(["user:x"], ws)

    conn.dead = True
    await mgr._send_from_backend(["user:x"], b"push")

    assert ws.application_state == WebSocketState.DISCONNECTED
    assert ws not in mgr.rooms["user:x"]
    assert not is_ws_connected(ws)

    # The endpoint's receive loop must see a disconnect, not a RuntimeError.
    with pytest.raises(WebSocketDisconnect) as exc:
        await receive_ws_text(ws)
    assert exc.value.code == 1006

    # And its finally block must be able to clean up without masking anything.
    await mgr.remove_websocket(ws)
    assert ws not in mgr.all_websockets


async def test_broadcast_to_dropped_client_then_remove_does_not_raise() -> None:
    ws, conn = await accepted_ws()
    mgr = make_manager()
    await mgr.add_websocket(ws)
    await mgr.join(["user:x"], ws)

    conn.dead = True
    await mgr._broadcast_from_backend(b"push")

    assert ws not in mgr.all_websockets
    with pytest.raises(WebSocketDisconnect):
        await receive_ws_bytes(ws)

    # Previously raised KeyError: the broadcast had already dropped the socket.
    await mgr.remove_websocket(ws)
    assert ws not in mgr.rooms["user:x"]


async def test_sockets_no_longer_connected_are_skipped_and_dropped() -> None:
    ws, conn = await accepted_ws()
    healthy, healthy_conn = await accepted_ws()
    mgr = make_manager()
    for sock in (ws, healthy):
        await mgr.add_websocket(sock)
        await mgr.join(["room"], sock)

    conn.dead = True
    await mgr._send_from_backend(["room"], b"first")
    conn.sent.clear()

    # Not attempted again on the dead socket; still delivered to the healthy one.
    await mgr._send_from_backend(["room"], b"second")
    await mgr._broadcast_from_backend(b"third")

    assert conn.data_sent() == []
    assert healthy_conn.data_sent() == [b"first", b"second", b"third"]
    assert mgr.rooms["room"] == {healthy}
    assert mgr.all_websockets == {healthy}


async def test_socket_closed_by_app_is_not_sent_to() -> None:
    ws, conn = await accepted_ws()
    mgr = make_manager()
    await mgr.add_websocket(ws)
    await mgr.join(["room"], ws)

    await ws.close()
    conn.sent.clear()
    await mgr._send_from_backend(["room"], b"push")
    await mgr._broadcast_from_backend(b"push")

    assert conn.sent == []
    assert ws not in mgr.rooms["room"]
    assert ws not in mgr.all_websockets


async def test_receive_helpers_pass_through_messages_and_client_disconnect() -> None:
    ws, conn = await accepted_ws()
    conn.inbox += [
        {"type": "websocket.receive", "text": "hi"},
        {"type": "websocket.receive", "text": '{"a": 1}'},
        {"type": "websocket.receive", "bytes": b"raw"},
        {"type": "websocket.disconnect", "code": 1001},
    ]

    assert await receive_ws_text(ws) == "hi"
    assert await receive_ws_json(ws) == {"a": 1}
    assert await receive_ws_bytes(ws) == b"raw"
    with pytest.raises(WebSocketDisconnect) as exc:
        await receive_ws_text(ws)
    assert exc.value.code == 1001
    assert not is_ws_connected(ws)


async def test_receive_helpers_still_reject_unaccepted_socket() -> None:
    conn = FakeConnection()
    scope = {"type": "websocket", "path": "/ws", "headers": [], "query_string": b""}
    ws = WebSocket(scope, conn.receive, conn.send)
    with pytest.raises(RuntimeError):
        await receive_ws_text(ws)


async def test_failed_room_push_forgets_socket_everywhere() -> None:
    ws, conn = await accepted_ws()
    mgr = make_manager()
    await mgr.add_websocket(ws)
    await mgr.join(["user:x", "tenant:y"], ws)

    conn.dead = True
    await mgr._send_from_backend(["user:x"], b"push")

    # A dead socket is useless in every room, not only the one that was pushed to.
    assert ws not in mgr.rooms["user:x"]
    assert ws not in mgr.rooms["tenant:y"]
    assert ws not in mgr.all_websockets


async def test_socket_in_several_targeted_rooms_gets_one_copy() -> None:
    ws, conn = await accepted_ws()
    mgr = make_manager()
    await mgr.add_websocket(ws)
    await mgr.join(["a", "b"], ws)

    await mgr._send_from_backend(["a", "b"], b"push")

    assert conn.data_sent() == [b"push"]
