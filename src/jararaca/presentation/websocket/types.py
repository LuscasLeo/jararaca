# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from jararaca.presentation.websocket.base_types import WebSocketMessageBase
from jararaca.presentation.websocket.context import use_ws_message_sender


class WebSocketMessage(WebSocketMessageBase):

    async def send(self, *rooms: str) -> None:
        await use_ws_message_sender().send(list(rooms), self)
