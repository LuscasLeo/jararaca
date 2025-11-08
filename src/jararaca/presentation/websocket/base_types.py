# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import ClassVar

from pydantic import BaseModel


class WebSocketMessageBase(BaseModel):

    MESSAGE_ID: ClassVar[str] = "__UNSET__"
