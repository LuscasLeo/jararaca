# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from abc import ABC, abstractmethod
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import datetime, tzinfo
from typing import Any, ClassVar, Generator, Literal

from pydantic import BaseModel


class IMessage(BaseModel):
    """
    Base class for messages representing tasks.
    A Task is a message that represents a unit of work to be done.
    It is published to a TaskPublisher and consumed by a TaskHandler, wrapped in TaskData.
    Note: A Task is not an Event.
    """

    MESSAGE_TOPIC: ClassVar[str] = "__unset__"

    MESSAGE_TYPE: ClassVar[Literal["task", "event"]] = "task"

    MESSAGE_CATEGORY: ClassVar[str] = "uncategorized"


DelayedMessageIdempotencyPayloadPolicy = Literal["ignore", "replace"]
"""
    DelayedMessageIdempotencyPayloadPolicy defines the policy for handling idempotent delayed messages with different payloads.
    - "ignore": If a delayed message with the same idempotency key already exists, the new message will be ignored, even if the payload is different.
    - "replace": If a delayed message with the same idempotency key already exists, the new message will replace the existing one, even if the payload is different.
"""

DelayedMessageIdempotencyTimePolicy = Literal["replace", "greater", "lesser"]
"""
    DelayedMessageIdempotencyTimePolicy defines the policy for handling idempotent delayed messages with different dispatch times.
    - "replace": If a delayed message with the same idempotency key already exists, the new message will replace the existing one, even if the dispatch time is different.
    - "greater": If a delayed message with the same idempotency key already exists, the new message will replace the existing one only if the dispatch time is greater than the existing one.
    - "lesser": If a delayed message with the same idempotency key already exists, the new message will replace the existing one only if the dispatch time is lesser than the existing one.
"""


class InternalMessagePublisher(ABC):
    @abstractmethod
    async def publish(self, message: IMessage, topic: str) -> None:
        """
        Immediately enqueue a message under the given topic.

        The message is staged and will be dispatched to the broker once the
        active interceptor commits the transaction (transactional-outbox pattern).

        Args:
            message: The message payload to publish.
            topic:   The routing topic (queue / exchange key) to publish to.
        """

    @abstractmethod
    async def delay(
        self,
        message: IMessage,
        seconds: int,
        *,
        idempotency_key: str | None = None,
        payload_policy: DelayedMessageIdempotencyPayloadPolicy = "ignore",
        time_policy: DelayedMessageIdempotencyTimePolicy = "replace",
    ) -> None:
        """
        Enqueue a message to be delivered after a relative delay.

        The message will be dispatched ``seconds`` seconds from now. Useful for
        implementing retry back-off, deferred side-effects, or cool-down periods
        without blocking the current request.

        When ``idempotency_key`` is provided, ``payload_policy`` controls what
        happens if a pending message with the same key already exists but carries
        a different payload, and ``time_policy`` controls what happens when the
        existing dispatch time differs from the new one.

        Args:
            message:         The message payload to publish.
            seconds:         Number of seconds to wait before delivery.
            idempotency_key: Optional unique key to deduplicate this operation.
            payload_policy:  How to handle an existing entry with a different payload
                             (``"ignore"`` keeps the old payload, ``"replace"`` overwrites it).
            time_policy:     How to handle an existing entry with a different dispatch time
                             (``"replace"`` always overwrites; ``"greater"`` keeps the later
                             time; ``"lesser"`` keeps the earlier time).
        """

    @abstractmethod
    async def schedule(
        self,
        message: IMessage,
        when: datetime,
        timezone: tzinfo,
        *,
        idempotency_key: str | None = None,
        payload_policy: DelayedMessageIdempotencyPayloadPolicy = "ignore",
        time_policy: DelayedMessageIdempotencyTimePolicy = "replace",
    ) -> None:
        """
        Enqueue a message to be delivered at an absolute point in time.

        The message will be dispatched at ``when``, interpreted in ``timezone``.
        Suitable for calendar-driven workflows such as sending reminders, expiry
        notices, or time-boxed promotions.

        When ``idempotency_key`` is provided, ``payload_policy`` controls what
        happens if a pending message with the same key already exists but carries
        a different payload, and ``time_policy`` controls what happens when the
        existing dispatch time differs from the new one.

        Args:
            message:         The message payload to publish.
            when:            The exact datetime at which the message should be delivered.
            timezone:        The timezone used to interpret ``when``.
            idempotency_key: Optional unique key to deduplicate this operation.
            payload_policy:  How to handle an existing entry with a different payload
                             (``"ignore"`` keeps the old payload, ``"replace"`` overwrites it).
            time_policy:     How to handle an existing entry with a different dispatch time
                             (``"replace"`` always overwrites; ``"greater"`` keeps the later
                             time; ``"lesser"`` keeps the earlier time).
        """

    @abstractmethod
    async def flush(self) -> None:
        """
        Publish all messages that have been delayed or scheduled.
        This is typically called at the end of a request or task processing.
        """


message_publishers_ctx = ContextVar[dict[str, InternalMessagePublisher]](
    "message_publishers_ctx", default={}
)


@contextmanager
def provide_message_publisher(
    connection_name: str, message_publisher: InternalMessagePublisher
) -> Generator[None, Any, None]:

    current_map = message_publishers_ctx.get({})

    token = message_publishers_ctx.set(
        {**current_map, connection_name: message_publisher}
    )

    try:
        yield
    finally:
        with suppress(ValueError):
            message_publishers_ctx.reset(token)


def use_publisher(connecton_name: str = "default") -> InternalMessagePublisher:
    publisher = message_publishers_ctx.get({}).get(connecton_name)
    if publisher is None:
        raise ValueError(f"MessagePublisher not found for connection {connecton_name}")

    return publisher
