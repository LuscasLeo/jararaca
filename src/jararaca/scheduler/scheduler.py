import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

import uvloop
from croniter import croniter

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.lifecycle import AppLifecycle
from jararaca.microservice import Microservice
from jararaca.scheduler.decorators import ScheduledAction


@dataclass
class SchedulerConfig:
    interval: int


class SchedulerBackend: ...


def extract_scheduled_actions(
    app: Microservice, container: Container
) -> list[tuple[Callable[..., Any], "ScheduledAction"]]:
    scheduled_actions: list[tuple[Callable[..., Any], "ScheduledAction"]] = []
    for controllers in app.controllers:

        controller_instance: Any = container.get_by_type(controllers)

        controller_scheduled_actions = ScheduledAction.get_type_scheduled_actions(
            controller_instance
        )
        scheduled_actions.extend(controller_scheduled_actions)

    return scheduled_actions


# TODO: Implement Backend for Distributed Lock
# TODO: Improve error handling
# TODO: Implement logging
# TODO: Implement tests
# TODO: Implement graceful shutdown
# TODO: Implement ScheduletAction parameters configuration
class Scheduler:

    def __init__(
        self,
        app: Microservice,
        backend: SchedulerBackend,
        config: SchedulerConfig,
    ) -> None:
        self.app = app
        self.backend = backend
        self.config = config
        self.container = Container(self.app)
        self.scheduled_actions = extract_scheduled_actions(app, self.container)
        self.uow_provider = asynccontextmanager(
            UnitOfWorkContextProvider(app, self.container)
        )

        self.tasks: set[asyncio.Task[Any]] = set()
        self.lock = asyncio.Lock()
        self.shutdown_event = asyncio.Event()

        self.last_executions: dict[Callable[..., Any], datetime] = {}

        self.lifceycle = AppLifecycle(app, self.container)

    async def process_task(
        self, func: Callable[..., Any], scheduled_action: ScheduledAction
    ) -> None:

        async with self.lock:
            task = asyncio.create_task(self.handle_task(func, scheduled_action))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def handle_task(
        self, func: Callable[..., Any], scheduled_action: ScheduledAction
    ) -> None:

        if func in self.last_executions:
            last_execution = self.last_executions[func]
            if scheduled_action.cron:
                cron = croniter(scheduled_action.cron, last_execution)
                next_run: datetime = cron.get_next(datetime)
                if next_run > datetime.now(UTC):
                    return

        try:
            async with self.uow_provider():
                await func()
        except Exception as e:
            logging.error(f"Error in scheduled action {scheduled_action}: {e}")

        self.last_executions[func] = datetime.now(UTC)

    def run(self) -> None:

        async def run_scheduled_actions() -> None:

            async with self.lifceycle():
                while True:
                    for func, scheduled_action in self.scheduled_actions:
                        if self.shutdown_event.is_set():
                            break

                        await self.process_task(func, scheduled_action)

                    await asyncio.sleep(self.config.interval)

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(run_scheduled_actions())
