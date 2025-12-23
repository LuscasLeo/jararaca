# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Annotated

from pydantic import BaseModel

from jararaca.core.providers import Token


class GlobalSchedulerConfig(BaseModel):
    MAX_CONCURRENT_JOBS: int = 10


GlobalSchedulerConfigToken = Token.create(
    GlobalSchedulerConfig, "GlobalSchedulerConfig"
)
GlobalSchedulerConfigAnnotated = Annotated[
    GlobalSchedulerConfig, GlobalSchedulerConfigToken
]
