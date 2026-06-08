# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

from jararaca.messagebus.publisher import (
    DelayedMessageIdempotencyPayloadPolicy,
    DelayedMessageIdempotencyTimePolicy,
    IMessage,
    InternalMessagePublisher,
)


@dataclass
class CollectorDelayedMessageData:
    message: IMessage
    when: datetime
    timezone: tzinfo
    idempotency_key: str | None = None
    payload_policy: DelayedMessageIdempotencyPayloadPolicy = "ignore"
    time_policy: DelayedMessageIdempotencyTimePolicy = "replace"


class MessagePublisherCollector(InternalMessagePublisher):

    def __init__(self) -> None:
        self.staged_delayed_messages: list[CollectorDelayedMessageData] = []
        self.staged_messages: list[IMessage] = []

    async def publish(self, message: IMessage, topic: str) -> None:
        self.staged_messages.append(message)

    async def delay(
        self,
        message: IMessage,
        seconds: int,
        *,
        idempotency_key: str | None = None,
        payload_policy: DelayedMessageIdempotencyPayloadPolicy = "ignore",
        time_policy: DelayedMessageIdempotencyTimePolicy = "replace",
    ) -> None:
        self.staged_delayed_messages.append(
            CollectorDelayedMessageData(
                message=message,
                when=datetime.now(UTC) + timedelta(seconds=seconds),
                timezone=UTC,
                idempotency_key=idempotency_key,
                payload_policy=payload_policy,
                time_policy=time_policy,
            )
        )

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
        self.staged_delayed_messages.append(
            CollectorDelayedMessageData(
                message=message,
                when=when,
                timezone=timezone,
                idempotency_key=idempotency_key,
                payload_policy=payload_policy,
                time_policy=time_policy,
            )
        )

    async def fill(self, publisher: InternalMessagePublisher) -> None:
        for message in self.staged_messages:
            await publisher.publish(message, message.MESSAGE_TOPIC)

        for delayed_message in self.staged_delayed_messages:
            await publisher.schedule(
                delayed_message.message,
                delayed_message.when,
                delayed_message.timezone,
                idempotency_key=delayed_message.idempotency_key,
                payload_policy=delayed_message.payload_policy,
                time_policy=delayed_message.time_policy,
            )

    def has_messages(self) -> bool:
        return bool(self.staged_messages or self.staged_delayed_messages)

    async def flush(self) -> None:
        raise NotImplementedError("I'm just a poor little collector! :(")
