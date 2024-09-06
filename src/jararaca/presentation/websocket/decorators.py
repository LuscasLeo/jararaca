from typing import TypedDict, TypeVar, cast

DECORATED_CLASS = TypeVar("DECORATED_CLASS")


class WebSocketEndpointOptions(TypedDict): ...


class WebSocketEndpoint:

    WEBSOCKET_ENDPOINT_ATTR = "__websocket_endpoint__"

    def __init__(self, path: str, options: WebSocketEndpointOptions = {}) -> None:
        self.path = path
        self.options = options

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
