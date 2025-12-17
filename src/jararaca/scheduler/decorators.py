# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
from dataclasses import dataclass
from types import FunctionType
from typing import Any, Awaitable, Callable, TypeVar

from jararaca.reflect.controller_inspect import (
    ControllerMemberReflect,
    inspect_controller,
)
from jararaca.reflect.decorators import StackableDecorator

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Any])


class ScheduledAction(StackableDecorator):

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

    members: list[tuple[str, FunctionType]] = []
    for name, value in inspect.getmembers_static(
        instance, predicate=inspect.isfunction
    ):

        members.append((name, value))

    scheduled_actions: list[ScheduledActionData] = []

    for name, member in members:
        scheduled_action = ScheduledAction.get_last(member)

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
