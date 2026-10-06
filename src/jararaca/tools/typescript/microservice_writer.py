# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from io import StringIO
from typing import Any

from jararaca.microservice import Microservice
from jararaca.presentation.decorators import RestController
from jararaca.presentation.websocket.decorators import RegisterWebSocketMessage
from jararaca.presentation.websocket.websocket_interceptor import (
    WebSocketMessageWrapper,
)
from jararaca.tools.typescript.decorators import ExposeType
from jararaca.tools.typescript.http.controller_writer import (
    write_rest_controller_to_typescript_interface,
)
from jararaca.tools.typescript.model_interfaces import (
    parse_type_to_typescript_interface,
)
from jararaca.tools.typescript.parse_context import with_parse_context
from jararaca.tools.typescript.prelude import read_typescript_prelude
from jararaca.tools.typescript.type_introspection import is_primitive
from jararaca.tools.typescript.websocket_writer import (
    write_websocket_message_interfaces,
    write_websocket_message_map,
)


def write_type_interfaces(root_types: set[Any]) -> str:
    """
    Generate interfaces for the given types and, transitively, for every type they
    reference. The output is sorted by type name to be deterministic.
    """
    processed_types: set[Any] = set()
    backlog: set[Any] = root_types.copy()

    unordered_types_buffer: list[tuple[type, str]] = []

    while len(backlog) > 0:
        t = backlog.pop()
        if not is_primitive(t):
            with with_parse_context() as parse_context:

                identified_types, text = parse_type_to_typescript_interface(t)
                processed_types.add(t)
                unordered_types_buffer.append((t, text))
                identified_types.update(parse_context.mapped_types)
                backlog.update(identified_types - processed_types)

    ordered_types_buffer = sorted(unordered_types_buffer, key=lambda x: x[0].__name__)

    return "".join(text for _, text in ordered_types_buffer)


def write_microservice_to_typescript_interface(
    microservice: Microservice,
) -> str:
    rest_controller_buffer = StringIO()
    mapped_types_set: set[Any] = set()

    websocket_registries: set[RegisterWebSocketMessage] = set()
    mapped_types_set.add(WebSocketMessageWrapper)

    # Add all explicitly exposed types
    mapped_types_set.update(ExposeType.get_all_exposed_types())

    for controller in microservice.controllers:
        rest_controller = RestController.get_last(controller)

        if rest_controller is None:
            continue

        controller_class_strio, types, hooks_strio = (
            write_rest_controller_to_typescript_interface(
                rest_controller,
                controller,
            )
        )

        mapped_types_set.update(types)
        rest_controller_buffer.write(controller_class_strio.getvalue())
        if hooks_strio is not None:
            rest_controller_buffer.write(hooks_strio.getvalue())

        registered = RegisterWebSocketMessage.get_last(controller)

        if registered is not None:
            identified_types, text = write_websocket_message_interfaces(registered)
            websocket_registries.add(registered)
            mapped_types_set.update(identified_types)
            rest_controller_buffer.write(text)

    final_buffer = StringIO()

    final_buffer.write(read_typescript_prelude())
    final_buffer.write(write_websocket_message_map(websocket_registries))

    final_buffer.write(write_type_interfaces(mapped_types_set))

    final_buffer.write(rest_controller_buffer.getvalue())

    return final_buffer.getvalue()
