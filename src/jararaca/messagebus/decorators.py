import inspect
from typing import Any, Callable, TypeVar, cast

from jararaca.messagebus import Message

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Any])
DECORATED_CLASS = TypeVar("DECORATED_CLASS", bound=Any)


MESSAGEBUS_INCOMING_MAP = dict[str, Callable[[Message[Any]], Any]]


class MessageBusController:

    MESSAGEBUS_ATTR = "__messagebus__"

    def __init__(self) -> None:
        self.messagebus_factory: Callable[[Any], MESSAGEBUS_INCOMING_MAP] | None = None

    def get_messagebus_factory(
        self,
    ) -> Callable[[DECORATED_CLASS], MESSAGEBUS_INCOMING_MAP]:
        if self.messagebus_factory is None:
            raise Exception("MessageBus factory is not set")
        return self.messagebus_factory

    def __call__(self, func: type[DECORATED_CLASS]) -> type[DECORATED_CLASS]:

        def messagebus_factory(
            instance: DECORATED_CLASS,
        ) -> MESSAGEBUS_INCOMING_MAP:
            handlers: MESSAGEBUS_INCOMING_MAP = {}
            inspect.signature(func)

            members = inspect.getmembers(func, predicate=inspect.isfunction)

            for name, member in members:
                message_incoming = IncomingHandler.get_message_incoming(member)

                if message_incoming is None:
                    continue

                if not inspect.iscoroutinefunction(member):
                    raise Exception(
                        "Message incoming handler '%s' from '%s.%s' must be a coroutine function"
                        % (name, func.__module__, func.__qualname__)
                    )

                handlers[message_incoming.topic] = getattr(instance, name)

            return handlers

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


class IncomingHandler:

    MESSAGE_INCOMING_ATTR = "__message_incoming__"

    def __init__(
        self,
        topic: str,
        timeout: int | None = None,
        exception_handler: Callable[[BaseException], None] | None = None,
        nack_on_exception: bool = False,
        auto_ack: bool = True,
    ) -> None:
        self.topic = topic
        self.timeout = timeout
        self.exception_handler = exception_handler
        self.nack_on_exception = nack_on_exception
        self.auto_ack = auto_ack

    def __call__(self, func: DECORATED_FUNC) -> DECORATED_FUNC:

        IncomingHandler.register(func, self)

        return func

    @staticmethod
    def register(func: DECORATED_FUNC, message_incoming: "IncomingHandler") -> None:

        setattr(func, IncomingHandler.MESSAGE_INCOMING_ATTR, message_incoming)

    @staticmethod
    def get_message_incoming(func: DECORATED_FUNC) -> "IncomingHandler | None":
        if not hasattr(func, IncomingHandler.MESSAGE_INCOMING_ATTR):
            return None

        return cast(
            IncomingHandler, getattr(func, IncomingHandler.MESSAGE_INCOMING_ATTR)
        )
