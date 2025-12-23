# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio

from jararaca.helpers.global_scheduler.config import GlobalSchedulerConfigAnnotated
from jararaca.helpers.global_scheduler.registry import (
    GlobalScheduleFnType,
    GlobalSchedulerRegistry,
)
from jararaca.messagebus.decorators import MessageBusController
from jararaca.scheduler.decorators import ScheduledAction
from jararaca.utils.env_parse_utils import get_env_str

SCHEDULER_CRON = get_env_str("SCHEDULER_CRON", "*/5 * * * *")


@MessageBusController()
class GlobalSchedulerController:

    def __init__(self, config: GlobalSchedulerConfigAnnotated):
        self._config = config

    @ScheduledAction(cron=SCHEDULER_CRON)
    async def trigger_scheduled_actions(self) -> None:
        """Trigger all registered scheduled actions."""

        taks = []

        semaphore = asyncio.Semaphore(self._config.MAX_CONCURRENT_JOBS)

        for action in GlobalSchedulerRegistry.get_registered_actions():
            task = asyncio.create_task(self._run_with_semaphore(semaphore, action))

            taks.append(task)

    async def _run_with_semaphore(
        self, semaphore: asyncio.Semaphore, action: GlobalScheduleFnType
    ) -> None:
        async with semaphore:
            await action()
