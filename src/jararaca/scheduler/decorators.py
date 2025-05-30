import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar, cast

from jararaca.reflect.controller_inspect import (
    ControllerMemberReflect,
    inspect_controller,
)

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Any])


class ScheduledAction:
    SCHEDULED_ACTION_ATTR = "__scheduled_action__"

    def __init__(
        self,
        cron: str,
        allow_overlap: bool = False,
        exclusive: bool = True,
        timeout: int | None = None,
        exception_handler: Callable[[BaseException], None] | None = None,
        name: str | None = None,
    ) -> None:
        """
        :param cron: A string representing the cron expression for the scheduled action.
        :param allow_overlap: A boolean indicating if the scheduled action should new executions even if the previous one is still running.
        :param exclusive: A boolean indicating if the scheduled action should be executed in one instance of the application. (Requires a distributed lock provided by a backend)
        :param exception_handler: A callable that will be called when an exception is raised during the execution of the scheduled action.
        :param timeout: An integer representing the timeout for the scheduled action in seconds. If the scheduled action takes longer than this time, it will be terminated.
        :param name: An optional name for the scheduled action, used for filtering which actions to run.
        """
        self.cron = cron
        """
        A string representing the cron expression for the scheduled action.
        """

        self.allow_overlap = allow_overlap
        """
        A boolean indicating if the scheduled action should new executions even if the previous one is still running.
        """

        self.exclusive = exclusive
        """
        A boolean indicating if the scheduled action should be executed
        in one instance of the application. (Requires a distributed lock provided by a backend)
        """

        self.exception_handler = exception_handler
        """
        A callable that will be called when an exception is raised during the execution of the scheduled action.
        """

        self.timeout = timeout
        """
        An integer representing the timeout for the scheduled action in seconds.
        If the scheduled action takes longer than this time, it will be terminated.
        """

        self.name = name
        """
        An optional name for the scheduled action, used for filtering which actions to run.
        """

    def __call__(self, func: DECORATED_FUNC) -> DECORATED_FUNC:
        ScheduledAction.register(func, self)
        return func

    @staticmethod
    def register(func: DECORATED_FUNC, scheduled_action: "ScheduledAction") -> None:
        setattr(func, ScheduledAction.SCHEDULED_ACTION_ATTR, scheduled_action)

    @staticmethod
    def get_scheduled_action(func: DECORATED_FUNC) -> "ScheduledAction | None":
        if not hasattr(func, ScheduledAction.SCHEDULED_ACTION_ATTR):
            return None

        return cast(
            ScheduledAction, getattr(func, ScheduledAction.SCHEDULED_ACTION_ATTR)
        )

    @staticmethod
    def get_function_id(
        func: Callable[..., Any],
    ) -> str:
        """
        Get the function ID of the scheduled action.
        This is used to identify the scheduled action in the message broker.
        """
        return f"{func.__module__}.{func.__qualname__}"


@dataclass(frozen=True)
class ScheduledActionData:
    spec: ScheduledAction
    controller_member: ControllerMemberReflect
    callable: Callable[..., Awaitable[None]]


def get_type_scheduled_actions(
    instance: Any,
) -> list[ScheduledActionData]:

    _, member_metadata_map = inspect_controller(instance.__class__)

    members = inspect.getmembers(instance, predicate=inspect.ismethod)

    scheduled_actions: list[ScheduledActionData] = []

    for name, member in members:
        scheduled_action = ScheduledAction.get_scheduled_action(member)

        if scheduled_action is None:
            continue

        if name not in member_metadata_map:
            raise Exception(
                f"Member '{name}' is not a valid controller member in '{instance.__class__.__name__}'"
            )

        member_metadata = member_metadata_map[name]

        scheduled_actions.append(
            ScheduledActionData(
                callable=member,
                spec=scheduled_action,
                controller_member=member_metadata,
            )
        )

    return scheduled_actions
