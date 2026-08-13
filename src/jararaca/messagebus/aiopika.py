# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Any, Callable

from jararaca.messagebus.publisher import IMessage


def gen_queue_name(
    message_type: type[IMessage], instance_callable: Callable[..., Any]
) -> str:
    return f"{message_type.MESSAGE_TOPIC}.{instance_callable.__module__}.{instance_callable.__qualname__}"


def gen_routing_key(message_type: type[IMessage]) -> str:
    return f"{message_type.MESSAGE_TOPIC}.#"
