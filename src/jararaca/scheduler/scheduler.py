import asyncio
import inspect
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, AsyncContextManager, AsyncGenerator, Callable

import uvloop
from croniter import croniter

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.lifecycle import AppLifecycle
from jararaca.messagebus.decorators import ScheduleDispatchData
from jararaca.microservice import (
    AppTransactionContext,
    Microservice,
    SchedulerTransactionData,
)
from jararaca.scheduler.decorators import (
    ScheduledAction,
    ScheduledActionData,
    get_type_scheduled_actions,
)

logger = logging.getLogger(__name__)


@dataclass
class SchedulerConfig:
    interval: int


def extract_scheduled_actions(
    app: Microservice, container: Container, scheduler_names: set[str] | None = None
) -> list[ScheduledActionData]:
    scheduled_actions: list[ScheduledActionData] = []
    for controllers in app.controllers:

        controller_instance: Any = container.get_by_type(controllers)

        controller_scheduled_actions = get_type_scheduled_actions(controller_instance)

        # Filter scheduled actions by name if scheduler_names is provided
        if scheduler_names is not None:
            filtered_actions = []
            for action in controller_scheduled_actions:
                # Include actions that have a name and it's in the provided set
                if action.spec.name and action.spec.name in scheduler_names:
                    filtered_actions.append(action)
                # Skip actions without names when filtering is active
            controller_scheduled_actions = filtered_actions

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
        interval: int,
        scheduler_names: set[str] | None = None,
    ) -> None:
        self.app = app

        self.interval = interval
        self.scheduler_names = scheduler_names
        self.container = Container(self.app)
        self.uow_provider = UnitOfWorkContextProvider(app, self.container)

        self.tasks: set[asyncio.Task[Any]] = set()
        self.lock = asyncio.Lock()
        self.shutdown_event = asyncio.Event()

        self.last_checks: dict[Callable[..., Any], datetime] = {}

        self.lifceycle = AppLifecycle(app, self.container)

    async def process_task(self, sched_act_data: ScheduledActionData) -> None:

        async with self.lock:
            task = asyncio.create_task(self.handle_task(sched_act_data))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def handle_task(self, sched_act_data: ScheduledActionData) -> None:
        func = sched_act_data.callable
        scheduled_action = sched_act_data.spec
        last_check = self.last_checks.setdefault(func, datetime.now(UTC))

        cron = croniter(scheduled_action.cron, last_check)
        next_run: datetime = cron.get_next(datetime)
        if next_run > datetime.now(UTC):
            logger.info(
                f"Skipping {func.__module__}.{func.__qualname__} until {next_run}"
            )
            return

        logger.info(f"Running {func.__module__}.{func.__qualname__}")

        action_specs = ScheduledAction.get_scheduled_action(func)

        assert action_specs is not None

        ctx: AsyncContextManager[Any]
        if action_specs.timeout:
            ctx = asyncio.timeout(action_specs.timeout)
        else:
            ctx = none_context()

        try:
            async with self.uow_provider(
                app_context=AppTransactionContext(
                    controller_member_reflect=sched_act_data.controller_member,
                    transaction_data=SchedulerTransactionData(
                        scheduled_to=next_run,
                        cron_expression=scheduled_action.cron,
                        triggered_at=datetime.now(UTC),
                    ),
                )
            ):
                try:
                    async with ctx:
                        signature = inspect.signature(func)
                        if len(signature.parameters) > 0:
                            logging.warning(
                                f"Scheduled action {func.__module__}.{func.__qualname__} has parameters, but no arguments were provided. Must be using scheduler-v2"
                            )
                            await func(ScheduleDispatchData(time.time()))
                        else:
                            await func()

                except BaseException as e:
                    if action_specs.exception_handler:
                        action_specs.exception_handler(e)
                    else:
                        logging.exception(
                            f"Error in scheduled action {scheduled_action}: {e}"
                        )

        except Exception as e:
            logging.exception(f"Error in scheduled action {scheduled_action}: {e}")

        self.last_checks[func] = datetime.now(UTC)

    def run(self) -> None:

        async def run_scheduled_actions() -> None:

            async with self.lifceycle():
                scheduled_actions = extract_scheduled_actions(
                    self.app, self.container, self.scheduler_names
                )

                while True:
                    for action in scheduled_actions:
                        if self.shutdown_event.is_set():
                            break

                        await self.process_task(action)

                    await asyncio.sleep(self.interval)

        with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
            runner.run(run_scheduled_actions())


@asynccontextmanager
async def none_context() -> AsyncGenerator[None, None]:
    yield
