# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import TypedDict, TypeVar

from jararaca.presentation.websocket.base_types import WebSocketMessageBase
from jararaca.reflect.decorators import StackableDecorator

DECORATED_CLASS = TypeVar("DECORATED_CLASS")


class WebSocketEndpointOptions(TypedDict): ...


class WebSocketEndpoint(StackableDecorator):

    ORDER_COUNTER = 0

    def __init__(self, path: str, options: WebSocketEndpointOptions = {}) -> None:
        self.path = path
        self.options = options
        WebSocketEndpoint.ORDER_COUNTER += 1
        self.order = WebSocketEndpoint.ORDER_COUNTER


INHERITS_WS_MESSAGE = TypeVar("INHERITS_WS_MESSAGE", bound=WebSocketMessageBase)


class RegisterWebSocketMessage(StackableDecorator):

    def __init__(self, *message_types: type[INHERITS_WS_MESSAGE]) -> None:
        self.message_types = message_types
