from typing import TypedDict, TypeVar, cast

from jararaca.presentation.websocket.base_types import WebSocketMessageBase

DECORATED_CLASS = TypeVar("DECORATED_CLASS")


class WebSocketEndpointOptions(TypedDict): ...


class WebSocketEndpoint:

    WEBSOCKET_ENDPOINT_ATTR = "__websocket_endpoint__"
    ORDER_COUNTER = 0

    def __init__(self, path: str, options: WebSocketEndpointOptions = {}) -> None:
        self.path = path
        self.options = options
        WebSocketEndpoint.ORDER_COUNTER += 1
        self.order = WebSocketEndpoint.ORDER_COUNTER

    @staticmethod
    def register(cls: DECORATED_CLASS, instance: "WebSocketEndpoint") -> None:
        setattr(cls, WebSocketEndpoint.WEBSOCKET_ENDPOINT_ATTR, instance)

    @staticmethod
    def get(cls: DECORATED_CLASS) -> "WebSocketEndpoint | None":
        if not hasattr(cls, WebSocketEndpoint.WEBSOCKET_ENDPOINT_ATTR):
            return None

        return cast(
            WebSocketEndpoint, getattr(cls, WebSocketEndpoint.WEBSOCKET_ENDPOINT_ATTR)
        )

    def __call__(self, cls: DECORATED_CLASS) -> DECORATED_CLASS:
        WebSocketEndpoint.register(cls, self)
        return cls


INHERITS_WS_MESSAGE = TypeVar("INHERITS_WS_MESSAGE", bound=WebSocketMessageBase)


class RegisterWebSocketMessage:

    REGISTER_WEBSOCKET_MESSAGE_ATTR = "__register_websocket_message__"

    def __init__(self, *message_types: type[INHERITS_WS_MESSAGE]) -> None:
        self.message_types = message_types

    @staticmethod
    def register(cls: DECORATED_CLASS, instance: "RegisterWebSocketMessage") -> None:
        setattr(cls, RegisterWebSocketMessage.REGISTER_WEBSOCKET_MESSAGE_ATTR, instance)

    @staticmethod
    def get(cls: DECORATED_CLASS) -> "RegisterWebSocketMessage | None":
        if not hasattr(cls, RegisterWebSocketMessage.REGISTER_WEBSOCKET_MESSAGE_ATTR):
            return None

        return cast(
            RegisterWebSocketMessage,
            getattr(cls, RegisterWebSocketMessage.REGISTER_WEBSOCKET_MESSAGE_ATTR),
        )

    def __call__(self, cls: DECORATED_CLASS) -> DECORATED_CLASS:
        RegisterWebSocketMessage.register(cls, self)
        return cls
