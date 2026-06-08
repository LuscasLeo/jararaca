# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Literal

from pydantic import BaseModel


class DelayedMessageData(BaseModel):
    message_topic: str
    idempotency_key: str | None = None
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
    payload_policy: Literal["ignore", "replace"] = "ignore"
    time_policy: Literal["replace", "greater", "lesser"] = "replace"
    content_encoding: str | None = None
