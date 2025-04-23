import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar, cast

from jararaca.messagebus.message import INHERITS_MESSAGE_CO, Message, MessageOf
from jararaca.scheduler.decorators import ScheduledAction

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Any])
DECORATED_CLASS = TypeVar("DECORATED_CLASS", bound=Any)


class MessageHandler(Generic[INHERITS_MESSAGE_CO]):

    MESSAGE_INCOMING_ATTR = "__message_incoming__"

    def __init__(
        self,
        message: type[INHERITS_MESSAGE_CO],
        timeout: int | None = None,
        exception_handler: Callable[[BaseException], None] | None = None,
        nack_on_exception: bool = False,
        auto_ack: bool = True,
    ) -> None:
        self.message_type = message

        self.timeout = timeout
        self.exception_handler = exception_handler
        self.requeue_on_exception = nack_on_exception
        self.auto_ack = auto_ack

    def __call__(
        self, func: Callable[[Any, MessageOf[INHERITS_MESSAGE_CO]], Awaitable[None]]
    ) -> Callable[[Any, MessageOf[INHERITS_MESSAGE_CO]], Awaitable[None]]:

        MessageHandler[Any].register(func, self)

        return func

    @staticmethod
    def register(
        func: Callable[[Any, MessageOf[INHERITS_MESSAGE_CO]], Awaitable[None]],
        message_incoming: "MessageHandler[Any]",
    ) -> None:

        setattr(func, MessageHandler.MESSAGE_INCOMING_ATTR, message_incoming)

    @staticmethod
    def get_message_incoming(
        func: Callable[[MessageOf[Any]], Awaitable[Any]],
    ) -> "MessageHandler[Message] | None":
        if not hasattr(func, MessageHandler.MESSAGE_INCOMING_ATTR):
            return None

        return cast(
            MessageHandler[Message], getattr(func, MessageHandler.MESSAGE_INCOMING_ATTR)
        )


@dataclass(frozen=True)
class MessageHandlerData:
    message_type: type[Any]
    spec: MessageHandler[Message]
    callable: Callable[[MessageOf[Any]], Awaitable[None]]


@dataclass(frozen=True)
class ScheduleDispatchData:
    timestamp: float


@dataclass(frozen=True)
class ScheduledActionData:
    spec: ScheduledAction
    callable: Callable[
        ..., Awaitable[None]
    ]  # Callable[[ScheduleDispatchData], Awaitable[None]]


MESSAGE_HANDLER_DATA_SET = set[MessageHandlerData]
SCHEDULED_ACTION_DATA_SET = set[ScheduledActionData]


class MessageBusController:

    MESSAGEBUS_ATTR = "__messagebus__"

    def __init__(self) -> None:
        self.messagebus_factory: (
            Callable[[Any], tuple[MESSAGE_HANDLER_DATA_SET, SCHEDULED_ACTION_DATA_SET]]
            | None
        ) = None

    def get_messagebus_factory(
        self,
    ) -> Callable[
        [DECORATED_CLASS], tuple[MESSAGE_HANDLER_DATA_SET, SCHEDULED_ACTION_DATA_SET]
    ]:
        if self.messagebus_factory is None:
            raise Exception("MessageBus factory is not set")
        return self.messagebus_factory

    def __call__(self, func: type[DECORATED_CLASS]) -> type[DECORATED_CLASS]:

        def messagebus_factory(
            instance: DECORATED_CLASS,
        ) -> tuple[MESSAGE_HANDLER_DATA_SET, SCHEDULED_ACTION_DATA_SET]:
            handlers: MESSAGE_HANDLER_DATA_SET = set()

            schedulers: SCHEDULED_ACTION_DATA_SET = set()

            members = inspect.getmembers(func, predicate=inspect.isfunction)

            for name, member in members:
                message_handler_decoration = MessageHandler.get_message_incoming(member)
                scheduled_action_decoration = ScheduledAction.get_scheduled_action(
                    member
                )

                if message_handler_decoration is not None:

                    if not inspect.iscoroutinefunction(member):
                        raise Exception(
                            "Message incoming handler '%s' from '%s.%s' must be a coroutine function"
                            % (name, func.__module__, func.__qualname__)
                        )

                    handlers.add(
                        MessageHandlerData(
                            message_type=message_handler_decoration.message_type,
                            spec=message_handler_decoration,
                            callable=getattr(instance, name),
                        )
                    )
                elif scheduled_action_decoration is not None:
                    if not inspect.iscoroutinefunction(member):
                        raise Exception(
                            "Scheduled action handler '%s' from '%s.%s' must be a coroutine function"
                            % (name, func.__module__, func.__qualname__)
                        )

                    schedulers.add(
                        ScheduledActionData(
                            spec=scheduled_action_decoration,
                            callable=getattr(instance, name),
                        )
                    )

            return handlers, schedulers

        self.messagebus_factory = messagebus_factory

        MessageBusController.register(func, self)

        return func

    @staticmethod
    def register(
        func: type[DECORATED_CLASS], messagebus: "MessageBusController"
    ) -> None:

        setattr(func, MessageBusController.MESSAGEBUS_ATTR, messagebus)

    @staticmethod
    def get_messagebus(func: type[DECORATED_CLASS]) -> "MessageBusController | None":
        if not hasattr(func, MessageBusController.MESSAGEBUS_ATTR):
            return None

        return cast(
            MessageBusController, getattr(func, MessageBusController.MESSAGEBUS_ATTR)
        )
