# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Generation of the WebSocket message map and the message interfaces.
"""

from typing import Any, Iterable

from jararaca.presentation.websocket.decorators import RegisterWebSocketMessage
from jararaca.tools.typescript.model_interfaces import (
    parse_type_to_typescript_interface,
)


def write_websocket_message_interfaces(
    registered: RegisterWebSocketMessage,
) -> tuple[set[Any], str]:
    """
    Generate the interfaces of every message type of a registration.

    Returns the types found while parsing them and the generated TypeScript text.
    """
    mapped_types: set[Any] = set()
    buffer: list[str] = []
    for message_type in registered.message_types:
        identified_types, text = parse_type_to_typescript_interface(message_type)
        mapped_types.update(identified_types)
        buffer.append(text)
    return mapped_types, "".join(buffer)


def write_websocket_message_map(
    registries: Iterable[RegisterWebSocketMessage],
) -> str:
    """
    Generate the ``WebSocketMessageMap`` type mapping each MESSAGE_ID to its interface.
    """
    entries = sorted(
        f'\t"{message.MESSAGE_ID}": {message.__name__};'
        for registers in registries
        for message in registers.message_types
    )
    return "export type WebSocketMessageMap = {\n%s\n}\n" % "\n".join(entries)
