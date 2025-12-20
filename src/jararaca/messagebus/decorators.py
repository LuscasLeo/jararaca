# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later


import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, TypeVar, cast, get_args

from jararaca.messagebus.message import INHERITS_MESSAGE_CO, Message, MessageOf
from jararaca.reflect.controller_inspect import (
    ControllerMemberReflect,
    inspect_controller,
)
from jararaca.reflect.decorators import (
    DECORATED_T,
    GenericStackableDecorator,
    StackableDecorator,
)
from jararaca.reflect.helpers import is_generic_alias
from jararaca.scheduler.decorators import ScheduledAction, ScheduledActionData
from jararaca.utils.env_parse_utils import get_env_float, get_env_int, is_env_truffy
from jararaca.utils.retry import RetryPolicy

AcceptableHandler = (
    Callable[[Any, MessageOf[Any]], Awaitable[None]]
    | Callable[[Any, Any], Awaitable[None]]
)
MessageHandlerT = TypeVar("MessageHandlerT", bound=AcceptableHandler)

DEFAULT_TIMEOUT = get_env_int("JARARACA_MESSAGEBUS_HANDLER_TIMEOUT")
DEFAULT_NACK_ON_EXCEPTION = is_env_truffy("JARARACA_MESSAGEBUS_NACK_ON_EXCEPTION")
DEFAULT_AUTO_ACK = is_env_truffy("JARARACA_MESSAGEBUS_AUTO_ACK")
DEFAULT_NACK_DELAY_ON_EXCEPTION = get_env_float(
    "JARARACA_MESSAGEBUS_NACK_DELAY_ON_EXCEPTION"
)


class MessageHandler(GenericStackableDecorator[AcceptableHandler]):

    def __init__(
        self,
        message: type[INHERITS_MESSAGE_CO],
        *,
        timeout: int | None = DEFAULT_TIMEOUT if DEFAULT_TIMEOUT is not False else None,
        exception_handler: Callable[[BaseException], None] | None = None,
        nack_on_exception: bool = DEFAULT_NACK_ON_EXCEPTION,
        nack_delay_on_exception: float = DEFAULT_NACK_DELAY_ON_EXCEPTION or 5.0,
        auto_ack: bool = DEFAULT_AUTO_ACK,
        name: str | None = None,
        retry_config: RetryPolicy | None = None,
    ) -> None:
        self.message_type = message

        self.timeout = timeout
        self.exception_handler = exception_handler
        self.nack_on_exception = nack_on_exception
        self.nack_delay_on_exception = nack_delay_on_exception

        self.auto_ack = auto_ack
        self.name = name
        self.retry_config = retry_config

    def __call__(self, subject: MessageHandlerT) -> MessageHandlerT:
        return cast(MessageHandlerT, super().__call__(subject))

    def pre_decorated(self, subject: DECORATED_T) -> None:
        MessageHandler.validate_decorated_fn(subject)

    @staticmethod
    def validate_decorated_fn(
        subject: DECORATED_T,
    ) -> tuple[Literal["WRAPPED", "DIRECT"], type[Message]]:
        """Validates that the decorated function has the correct signature
        the decorated must follow one of the patterns:

        async def handler(self, message: MessageOf[YourMessageType]) -> None:
            ...

        async def handler(self, message: YourMessageType) -> None:
            ...

        """

        if not inspect.iscoroutinefunction(subject):
            raise RuntimeError(
                "Message handler '%s' must be a coroutine function"
                % (subject.__qualname__)
            )

        signature = inspect.signature(subject)

        parameters = list(signature.parameters.values())

        if len(parameters) != 2:
            raise RuntimeError(
                "Message handler '%s' must have exactly two parameters (self, message)"
                % (subject.__qualname__)
            )

        message_param = parameters[1]

        if message_param.annotation is inspect.Parameter.empty:
            raise RuntimeError(
                "Message handler '%s' must have type annotation for the message parameter"
                % (subject.__qualname__)
            )

        annotation_type = message_param.annotation
        mode: Literal["WRAPPED", "DIRECT"]
        if is_generic_alias(annotation_type):

            message_model_type = get_args(annotation_type)[0]

            mode = "WRAPPED"

        else:
            message_model_type = annotation_type

            mode = "DIRECT"

        if not inspect.isclass(message_model_type) or not issubclass(
            message_model_type, Message
        ):
            raise RuntimeError(
                "Message handler '%s' message parameter must be of type 'MessageOf[YourMessageType]' or 'YourMessageType' where 'YourMessageType' is a subclass of 'Message'"
                % (subject.__qualname__)
            )

        return mode, message_model_type


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
