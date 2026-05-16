# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pydantic import BaseModel


class DelayedMessageData(BaseModel):
    message_topic: str
    dispatch_time: int
    payload: bytes
    headers: dict[str, str | int | float | bool] | None = None
    # When set, the beat worker delivers the message directly to this queue via
    # the default exchange instead of fanning out through the topic exchange.
    # Used by handler retries so that only the failing handler's queue receives
    # the redelivery.
    target_queue: str | None = None
    message_id: str | None = None
    content_type: str | None = None
    content_encoding: str | None = None
