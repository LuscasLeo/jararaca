# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue
from aio_pika.exceptions import AMQPError, ChannelClosed, ChannelNotFoundEntity

logger = logging.getLogger(__name__)


class RabbitmqUtils:

    DEAD_LETTER_EXCHANGE = "dlx"
    DEAD_LETTER_QUEUE = "dlq"

    # Note: get_worker_v1_queue method is already defined above

    DEAD_LETTER_EXCHANGE = "dlx"
    DEAD_LETTER_QUEUE = "dlq"

    @classmethod
    async def get_dl_exchange(cls, channel: AbstractChannel) -> AbstractExchange:
        """
        Get the Dead Letter Exchange (DLX) for the given channel.
        """
        try:
            return await channel.get_exchange(
                cls.DEAD_LETTER_EXCHANGE,
            )
        except ChannelNotFoundEntity as e:
            logger.error(
                "Dead Letter Exchange '%s' does not exist. "
                "Please use the declare command to create it first. Error: %s",
                cls.DEAD_LETTER_EXCHANGE,
                e,
            )
            raise
        except ChannelClosed as e:
            logger.error(
                "Channel closed while getting Dead Letter Exchange '%s'. " "Error: %s",
                cls.DEAD_LETTER_EXCHANGE,
                e,
            )
            raise
        except AMQPError as e:
            logger.error(
                "AMQP error while getting Dead Letter Exchange '%s'. " "Error: %s",
                cls.DEAD_LETTER_EXCHANGE,
                e,
            )
            raise

    @classmethod
    async def declare_dl_exchange(
        cls, channel: AbstractChannel, passive: bool
    ) -> AbstractExchange:
        """
        Declare a Dead Letter Exchange (DLX) for the given channel.
        """

        return await channel.declare_exchange(
            cls.DEAD_LETTER_EXCHANGE,
            passive=passive,
            type="direct",
            durable=True,
            auto_delete=False,
        )

    @classmethod
    async def get_dl_queue(cls, channel: AbstractChannel) -> AbstractQueue:
        """
        Get the Dead Letter Queue (DLQ) for the given channel.
        """
        try:
            return await channel.get_queue(
                cls.DEAD_LETTER_QUEUE,
            )
        except ChannelNotFoundEntity as e:
            logger.error(
                "Dead Letter Queue '%s' does not exist. "
                "Please use the declare command to create it first. Error: %s",
                cls.DEAD_LETTER_QUEUE,
                e,
            )
            raise
        except ChannelClosed as e:
            logger.error(
                "Channel closed while getting Dead Letter Queue '%s'. " "Error: %s",
                cls.DEAD_LETTER_QUEUE,
                e,
            )
            raise
        except AMQPError as e:
            logger.error(
                "AMQP error while getting Dead Letter Queue '%s'. " "Error: %s",
                cls.DEAD_LETTER_QUEUE,
                e,
            )
            raise

    @classmethod
    async def declare_dl_queue(
        cls, channel: AbstractChannel, passive: bool
    ) -> AbstractQueue:
        """
        Declare a Dead Letter Queue (DLQ) for the given queue.
        """

        return await channel.declare_queue(
            cls.DEAD_LETTER_QUEUE,
            durable=True,
            passive=passive,
            arguments={
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": cls.DEAD_LETTER_EXCHANGE,
            },
        )

    @classmethod
    async def get_dl_kit(
        cls,
        channel: AbstractChannel,
    ) -> tuple[AbstractExchange, AbstractQueue]:
        """
        Get the Dead Letter Exchange and Queue (DLX and DLQ) for the given channel.
        """
        try:
            dlx = await cls.get_dl_exchange(channel)
            dlq = await cls.get_dl_queue(channel)
            return dlx, dlq
        except ChannelNotFoundEntity as e:
            logger.error(
                "Dead Letter infrastructure does not exist completely. "
                "Please use the declare command to create it first. Error: %s",
                e,
            )
            raise
        except ChannelClosed as e:
            logger.error(
                "Channel closed while getting Dead Letter infrastructure. " "Error: %s",
                e,
            )
            raise
        except AMQPError as e:
            logger.error(
                "AMQP error while getting Dead Letter infrastructure. " "Error: %s", e
            )
            raise

    @classmethod
    async def declare_dl_kit(
        cls,
        channel: AbstractChannel,
        passive: bool = False,
    ) -> tuple[AbstractExchange, AbstractQueue]:
        """
        Declare a Dead Letter Exchange and Queue (DLX and DLQ) for the given channel.
        """
        dlx = await cls.declare_dl_exchange(channel, passive=passive)
        dlq = await cls.declare_dl_queue(channel, passive=passive)
        await dlq.bind(dlx, routing_key=cls.DEAD_LETTER_EXCHANGE)
        return dlx, dlq

    @classmethod
    async def get_main_exchange(
        cls, channel: AbstractChannel, exchange_name: str
    ) -> AbstractExchange:
        """
        Get the main exchange for the given channel.
        """
        try:
            return await channel.get_exchange(exchange_name)
        except ChannelNotFoundEntity as e:
            logger.error(
                "Exchange '%s' does not exist. "
                "Please use the declare command to create it first. Error: %s",
                exchange_name,
                e,
            )
            raise
        except ChannelClosed as e:
            logger.error(
                "Channel closed while getting exchange '%s'. " "Error: %s",
                exchange_name,
                e,
            )
            raise
        except AMQPError as e:
            logger.error(
                "AMQP error while getting exchange '%s'. " "Error: %s", exchange_name, e
            )
            raise

    @classmethod
    async def declare_main_exchange(
        cls, channel: AbstractChannel, exchange_name: str, passive: bool
    ) -> AbstractExchange:
        """
        Declare a main exchange for the given channel.
        """

        return await channel.declare_exchange(
            exchange_name,
            passive=passive,
            type="topic",
            durable=True,
            auto_delete=False,
        )

    @classmethod
    async def get_queue(
        cls,
        channel: AbstractChannel,
        queue_name: str,
    ) -> AbstractQueue:
        """
        Get a queue with the given name.
        """
        try:
            return await channel.get_queue(queue_name)
        except ChannelNotFoundEntity as e:
            logger.error(
                "Queue '%s' does not exist. "
                "Please use the declare command to create it first. Error: %s",
                queue_name,
                e,
            )
            raise
        except ChannelClosed as e:
            logger.error(
                "Channel closed while getting queue '%s'. " "Error: %s", queue_name, e
            )
            raise
        except AMQPError as e:
            logger.error(
                "AMQP error while getting queue '%s'. " "Error: %s", queue_name, e
            )
            raise

    @classmethod
    async def declare_worker_queue(
        cls,
        channel: AbstractChannel,
        queue_name: str,
        passive: bool = False,
    ) -> AbstractQueue:
        """
        Declare a queue with the given name and properties.
        """

        return await channel.declare_queue(
            queue_name,
            passive=passive,
            durable=True,
            arguments={
                "x-dead-letter-exchange": cls.DEAD_LETTER_EXCHANGE,
                "x-dead-letter-routing-key": cls.DEAD_LETTER_EXCHANGE,
            },
        )

    @classmethod
    async def get_scheduled_action_queue(
        cls,
        channel: AbstractChannel,
        queue_name: str,
    ) -> AbstractQueue:
        """
        Get a scheduled action queue.
        """
        try:
            return await channel.get_queue(queue_name)
        except ChannelNotFoundEntity as e:
            logger.error(
                "Scheduler queue '%s' does not exist. "
                "Please use the declare command to create it first. Error: %s",
                queue_name,
                e,
            )
            raise
        except ChannelClosed as e:
            logger.error(
                "Channel closed while getting scheduler queue '%s'. " "Error: %s",
                queue_name,
                e,
            )
            raise
        except AMQPError as e:
            logger.error(
                "AMQP error while getting scheduler queue '%s'. " "Error: %s",
                queue_name,
                e,
            )
            raise

    @classmethod
    async def declare_scheduled_action_queue(
        cls,
        channel: AbstractChannel,
        queue_name: str,
        passive: bool = False,
    ) -> AbstractQueue:
        """
        Declare a scheduled action queue with simple durable configuration.
        The queue has a max length of 1 to ensure only one scheduled task
        is processed at a time.
        """
        return await channel.declare_queue(
            name=queue_name,
            durable=True,
            passive=passive,
            arguments={
                "x-max-length": 1,
            },
        )

    @classmethod
    async def delete_exchange(
        cls,
        channel: AbstractChannel,
        exchange_name: str,
        if_unused: bool = False,
    ) -> None:
        """
        Delete an exchange.
        """
        try:
            await channel.exchange_delete(
                exchange_name=exchange_name,
                if_unused=if_unused,
            )
        except ChannelNotFoundEntity:
            # Exchange might not exist, which is fine
            logger.debug(
                "Exchange '%s' does not exist, nothing to delete.", exchange_name
            )
        except ChannelClosed as e:
            logger.warning(
                "Channel closed while deleting exchange '%s': %s", exchange_name, e
            )
        except AMQPError as e:
            logger.warning(
                "AMQP error while deleting exchange '%s': %s", exchange_name, e
            )

    @classmethod
    async def delete_queue(
        cls,
        channel: AbstractChannel,
        queue_name: str,
        if_unused: bool = False,
        if_empty: bool = False,
    ) -> None:
        """
        Delete a queue.
        """
        try:
            await channel.queue_delete(
                queue_name=queue_name,
                if_unused=if_unused,
                if_empty=if_empty,
            )
        except ChannelNotFoundEntity:
            # Queue might not exist, which is fine
            logger.debug("Queue '%s' does not exist, nothing to delete.", queue_name)
        except ChannelClosed as e:
            logger.warning(
                "Channel closed while deleting queue '%s': %s", queue_name, e
            )
        except AMQPError as e:
            logger.warning("AMQP error while deleting queue '%s': %s", queue_name, e)
