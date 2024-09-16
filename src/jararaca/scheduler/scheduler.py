import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, AsyncContextManager, AsyncGenerator, Callable

import uvloop
from croniter import croniter

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.lifecycle import AppLifecycle
from jararaca.microservice import Microservice, SchedulerAppContext
from jararaca.scheduler.decorators import ScheduledAction

logger = logging.getLogger(__name__)


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
        self.uow_provider = UnitOfWorkContextProvider(app, self.container)

        self.tasks: set[asyncio.Task[Any]] = set()
        self.lock = asyncio.Lock()
        self.shutdown_event = asyncio.Event()

        self.last_checks: dict[Callable[..., Any], datetime] = {}

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

        last_check = self.last_checks.setdefault(func, datetime.now(UTC))

        cron = croniter(scheduled_action.cron, last_check)
        next_run: datetime = cron.get_next(datetime)
        if next_run > datetime.now(UTC):
            logger.info(
                f"Skipping {func.__module__}.{func.__qualname__} until {next_run}"
            )
            return

        action_specs = ScheduledAction.get_scheduled_action(func)

        assert action_specs is not None

        ctx: AsyncContextManager[Any]
        if action_specs.timeout:
            ctx = asyncio.timeout(action_specs.timeout)
        else:
            ctx = none_context()

        try:
            async with self.uow_provider(
                SchedulerAppContext(
                    action=func,
                    scheduled_to=next_run,
                    cron_expression=scheduled_action.cron,
                    triggered_at=datetime.now(UTC),
                )
            ):
                try:
                    async with ctx:
                        await func()
                except BaseException as e:
                    if action_specs.exception_handler:
                        action_specs.exception_handler(e)

        except Exception as e:
            logging.exception(f"Error in scheduled action {scheduled_action}: {e}")

        self.last_checks[func] = datetime.now(UTC)

    def run(self) -> None:

        async def run_scheduled_actions() -> None:

            async with self.lifceycle():
                scheduled_actions = extract_scheduled_actions(self.app, self.container)

                while True:
                    for func, scheduled_action in scheduled_actions:
                        if self.shutdown_event.is_set():
                            break

                        await self.process_task(func, scheduled_action)

                    await asyncio.sleep(self.config.interval)

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(run_scheduled_actions())


@asynccontextmanager
async def none_context() -> AsyncGenerator[None, None]:
    yield
