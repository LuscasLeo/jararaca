# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later


import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from jararaca.messagebus.message import INHERITS_MESSAGE_CO
from jararaca.reflect.controller_inspect import (
    ControllerMemberReflect,
    inspect_controller,
)
from jararaca.reflect.decorators import DECORATED_T, StackableDecorator
from jararaca.scheduler.decorators import ScheduledAction, ScheduledActionData


class MessageHandler(StackableDecorator):

    def __init__(
        self,
        message: type[INHERITS_MESSAGE_CO],
        timeout: int | None = None,
        exception_handler: Callable[[BaseException], None] | None = None,
        nack_on_exception: bool = False,
        auto_ack: bool = False,
        name: str | None = None,
    ) -> None:
        self.message_type = message

        self.timeout = timeout
        self.exception_handler = exception_handler
        self.nack_on_exception = nack_on_exception
        self.auto_ack = auto_ack
        self.name = name


@dataclass(frozen=True)
class MessageHandlerData:
    message_type: type[Any]
    spec: MessageHandler
    instance_callable: Callable[..., Awaitable[None]]
    controller_member: ControllerMemberReflect


@dataclass(frozen=True)
class ScheduleDispatchData:
    timestamp: float


SCHEDULED_ACTION_DATA_SET = set[ScheduledActionData]

MESSAGE_HANDLER_DATA_SET = set[MessageHandlerData]


class MessageBusController(StackableDecorator):

    def __init__(
        self,
        *,
        inherit_class_decorators: bool = True,
        inherit_methods_decorators: bool = True,
    ) -> None:
        self.messagebus_factory: (
            Callable[[Any], tuple[MESSAGE_HANDLER_DATA_SET, SCHEDULED_ACTION_DATA_SET]]
            | None
        ) = None

        self.inherit_class_decorators = inherit_class_decorators
        self.inherit_methods_decorators = inherit_methods_decorators

    def get_messagebus_factory(
        self,
    ) -> Callable[
        [DECORATED_T], tuple[MESSAGE_HANDLER_DATA_SET, SCHEDULED_ACTION_DATA_SET]
    ]:
        if self.messagebus_factory is None:
            raise Exception("MessageBus factory is not set")
        return self.messagebus_factory

    def post_decorated(self, subject: DECORATED_T) -> None:

        def messagebus_factory(
            instance: DECORATED_T,
        ) -> tuple[MESSAGE_HANDLER_DATA_SET, SCHEDULED_ACTION_DATA_SET]:
            handlers: MESSAGE_HANDLER_DATA_SET = set()

            schedulers: SCHEDULED_ACTION_DATA_SET = set()

            assert inspect.isclass(
                subject
            ), "MessageBusController can only be applied to classes"

            _, members = inspect_controller(subject)

            for name, member in members.items():
                message_handler_decoration = MessageHandler.get_last(
                    member.member_function
                )
                scheduled_action_decoration = ScheduledAction.get_last(
                    member.member_function
                )

                if message_handler_decoration is not None:

                    if not inspect.iscoroutinefunction(member.member_function):
                        raise Exception(
                            "Message incoming handler '%s' from '%s.%s' must be a coroutine function"
                            % (name, subject.__module__, subject.__qualname__)
                        )

                    handlers.add(
                        MessageHandlerData(
                            message_type=message_handler_decoration.message_type,
                            spec=message_handler_decoration,
                            instance_callable=getattr(instance, name),
                            controller_member=member,
                        )
                    )
                elif scheduled_action_decoration is not None:
                    if not inspect.iscoroutinefunction(member.member_function):
                        raise Exception(
                            "Scheduled action handler '%s' from '%s.%s' must be a coroutine function"
                            % (name, subject.__module__, subject.__qualname__)
                        )
                    instance_callable = getattr(instance, name)

                    schedulers.add(
                        ScheduledActionData(
                            controller_member=member,
                            spec=scheduled_action_decoration,
                            callable=instance_callable,
                        )
                    )

            return handlers, schedulers

        self.messagebus_factory = messagebus_factory
