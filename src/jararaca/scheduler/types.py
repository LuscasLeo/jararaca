# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pydantic import BaseModel


class DelayedMessageData(BaseModel):
    message_topic: str
    dispatch_time: int
    payload: bytes
