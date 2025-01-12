from jararaca.presentation.websocket.base_types import WebSocketMessageBase
from jararaca.presentation.websocket.context import use_ws_manager


class WebSocketMessage(WebSocketMessageBase):

    async def send(self, *rooms: str) -> None:
        await use_ws_manager().send(list(rooms), self)
