from contextlib import contextmanager, suppress
from contextvars import ContextVar
from typing import Generator, Protocol

from fastapi import WebSocket

from jararaca.presentation.websocket.base_types import WebSocketMessageBase


class WebSocketConnectionManager(Protocol):

    async def broadcast(self, message: bytes) -> None: ...
    async def send(self, rooms: list[str], message: WebSocketMessageBase) -> None: ...
    async def join(self, rooms: list[str], websocket: WebSocket) -> None: ...
    async def add_websocket(self, websocket: WebSocket) -> None: ...
    async def remove_websocket(self, websocket: WebSocket) -> None: ...


_ws_conn_manager_ctx = ContextVar[WebSocketConnectionManager]("ws_manage_ctx")


def use_ws_manager() -> WebSocketConnectionManager:
    try:
        return _ws_conn_manager_ctx.get()
    except LookupError:
        raise RuntimeError("No WebSocketConnectionManager found")


@contextmanager
def provide_ws_manager(
    ws_manager: WebSocketConnectionManager,
) -> Generator[None, None, None]:
    token = _ws_conn_manager_ctx.set(ws_manager)
    try:
        yield
    finally:
        with suppress(ValueError):
            _ws_conn_manager_ctx.reset(token)


class WebSocketMessageSender(Protocol):
    async def send(self, rooms: list[str], message: WebSocketMessageBase) -> None: ...


_ws_msg_sender_ctx = ContextVar[WebSocketMessageSender]("ws_msg_sender_ctx")


def use_ws_message_sender() -> WebSocketMessageSender:
    try:
        return _ws_msg_sender_ctx.get()
    except LookupError:
        raise RuntimeError("No WebSocketMessageSender found")


@contextmanager
def provide_ws_message_sender(
    ws_message_sender: WebSocketMessageSender,
) -> Generator[None, None, None]:
    token = _ws_msg_sender_ctx.set(ws_message_sender)
    try:
        yield
    finally:
        with suppress(ValueError):
            _ws_msg_sender_ctx.reset(token)
