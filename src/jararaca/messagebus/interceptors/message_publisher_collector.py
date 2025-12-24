# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

from jararaca.messagebus.publisher import IMessage, MessagePublisher


@dataclass
class CollectorDelayedMessageData:
    message: IMessage
    when: datetime
    timezone: tzinfo


class MessagePublisherCollector(MessagePublisher):

    def __init__(self) -> None:
        self.staged_delayed_messages: list[CollectorDelayedMessageData] = []
        self.staged_messages: list[IMessage] = []

    async def publish(self, message: IMessage, topic: str) -> None:
        self.staged_messages.append(message)

    async def delay(self, message: IMessage, seconds: int) -> None:
        self.staged_delayed_messages.append(
            CollectorDelayedMessageData(
                message=message,
                when=datetime.now(UTC) + timedelta(seconds=seconds),
                timezone=UTC,
            )
        )

    async def schedule(
        self, message: IMessage, when: datetime, timezone: tzinfo
    ) -> None:
        self.staged_delayed_messages.append(
            CollectorDelayedMessageData(
                message=message,
                when=when,
                timezone=timezone,
            )
        )

    async def fill(self, publisher: MessagePublisher) -> None:
        for message in self.staged_messages:
            await publisher.publish(message, message.MESSAGE_TOPIC)

        for delayed_message in self.staged_delayed_messages:
            await publisher.schedule(
                delayed_message.message,
                delayed_message.when,
                delayed_message.timezone,
            )

    def has_messages(self) -> bool:
        return bool(self.staged_messages or self.staged_delayed_messages)

    async def flush(self) -> None:
        raise NotImplementedError("I'm just a poor little collector! :(")
