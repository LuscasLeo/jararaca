import inspect
from typing import Any, Callable, TypeVar, cast

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Any])


class ScheduledAction:
    SCHEDULED_ACTION_ATTR = "__scheduled_action__"

    def __init__(
        self,
        cron: str,
        allow_overlap: bool = False,
        exclusive: bool = False,
        timeout: int | None = None,
        exception_handler: Callable[[BaseException], None] | None = None,
    ) -> None:
        """
        :param cron: A string representing the cron expression for the scheduled action.
        :param allow_overlap: A boolean indicating if the scheduled action should new executions even if the previous one is still running.
        :param exclusive: A boolean indicating if the scheduled action should be executed in one instance of the application. (Requires a distributed lock provided by a backend)
        """
        self.cron = cron
        self.allow_overlap = allow_overlap
        self.exclusive = exclusive
        self.exception_handler = exception_handler
        self.timeout = timeout

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
    def get_type_scheduled_actions(
        instance: Any,
    ) -> list[tuple[Callable[..., Any], "ScheduledAction"]]:

        members = inspect.getmembers(instance, predicate=inspect.ismethod)

        scheduled_actions: list[tuple[Callable[..., Any], "ScheduledAction"]] = []

        for _, member in members:
            scheduled_action = ScheduledAction.get_scheduled_action(member)

            if scheduled_action is None:
                continue

            scheduled_actions.append((member, scheduled_action))

        return scheduled_actions
