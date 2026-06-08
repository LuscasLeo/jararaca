# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import datetime, tzinfo
from typing import Generic, Protocol, TypeVar

from jararaca.messagebus.publisher import (
    DelayedMessageIdempotencyPayloadPolicy,
    DelayedMessageIdempotencyTimePolicy,
    IMessage,
    use_publisher,
)


class Message(IMessage):

    async def publish(self) -> None:
        """
        Publish the message immediately to the message bus.

        Retrieves the current publisher from the request context (via ``use_publisher``)
        and enqueues this message under the topic defined by ``MESSAGE_TOPIC``.
        The message is not actually dispatched until the active interceptor commits
        the transaction (transactional-outbox pattern).

        Raises:
            ValueError: If no publisher is bound to the current context.
        """
        task_publisher = use_publisher()
        await task_publisher.publish(self, self.MESSAGE_TOPIC)

    async def delay(
        self,
        seconds: int,
        *,
        idempotency_key: str | None = None,
        payload_policy: DelayedMessageIdempotencyPayloadPolicy = "ignore",
        time_policy: DelayedMessageIdempotencyTimePolicy = "replace",
    ) -> None:
        """
        Schedule the message to be published after a relative delay.

        Enqueues the message so that it will be delivered ``seconds`` seconds from now.
        Useful for implementing retry back-off, deferred side-effects, or cool-down
        periods without blocking the current request.

        An optional ``idempotency_key`` can be provided to prevent duplicate delivery
        if the same delay operation is issued more than once (e.g. on retries).
        When supplied, ``payload_policy`` and ``time_policy`` control how conflicts
        with an already-pending entry are resolved.

        Args:
            seconds:         Number of seconds to wait before the message is delivered.
            idempotency_key: Optional unique key to deduplicate this operation.
            payload_policy:  How to handle an existing entry with a different payload
                             (``"ignore"`` keeps the old payload, ``"replace"`` overwrites it).
            time_policy:     How to handle an existing entry with a different dispatch time
                             (``"replace"`` always overwrites; ``"greater"`` keeps the later
                             time; ``"lesser"`` keeps the earlier time).

        Raises:
            ValueError: If no publisher is bound to the current context.
        """
        task_publisher = use_publisher()
        await task_publisher.delay(
            self,
            seconds,
            idempotency_key=idempotency_key,
            payload_policy=payload_policy,
            time_policy=time_policy,
        )

    async def schedule(
        self,
        when: datetime,
        tz: tzinfo,
        *,
        idempotency_key: str | None = None,
        payload_policy: DelayedMessageIdempotencyPayloadPolicy = "ignore",
        time_policy: DelayedMessageIdempotencyTimePolicy = "replace",
    ) -> None:
        """
        Schedule the message to be published at an absolute point in time.

        Enqueues the message to be delivered at the datetime ``when``, interpreted
        in the timezone ``tz``.  Suitable for calendar-driven workflows such as
        sending reminders, expiry notices, or time-boxed promotions.

        An optional ``idempotency_key`` can be provided to prevent duplicate delivery
        if the same schedule operation is issued more than once (e.g. on retries).
        When supplied, ``payload_policy`` and ``time_policy`` control how conflicts
        with an already-pending entry are resolved.

        Args:
            when:            The exact datetime at which the message should be delivered.
            tz:              The timezone used to interpret ``when``.
            idempotency_key: Optional unique key to deduplicate this operation.
            payload_policy:  How to handle an existing entry with a different payload
                             (``"ignore"`` keeps the old payload, ``"replace"`` overwrites it).
            time_policy:     How to handle an existing entry with a different dispatch time
                             (``"replace"`` always overwrites; ``"greater"`` keeps the later
                             time; ``"lesser"`` keeps the earlier time).

        Raises:
            ValueError: If no publisher is bound to the current context.
        """
        task_publisher = use_publisher()
        await task_publisher.schedule(
            self,
            when,
            tz,
            idempotency_key=idempotency_key,
            payload_policy=payload_policy,
            time_policy=time_policy,
        )


INHERITS_MESSAGE_CO = TypeVar("INHERITS_MESSAGE_CO", bound=Message, covariant=True)


class MessageOf(Protocol, Generic[INHERITS_MESSAGE_CO]):

    def payload(self) -> INHERITS_MESSAGE_CO: ...
