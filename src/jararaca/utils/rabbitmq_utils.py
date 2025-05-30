from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue


class RabbitmqUtils:

    DEAD_LETTER_EXCHANGE = "dlx"
    DEAD_LETTER_QUEUE = "dlq"

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
    async def declare_queue(
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
    async def declare_worker_v1_queue(
        cls,
        channel: AbstractChannel,
        queue_name: str,
        dlx_name: str,
        dlq_name: str,
        passive: bool = False,
    ) -> AbstractQueue:
        """
        Declare a worker v1 queue with custom dead letter exchange and routing key.
        """
        return await channel.declare_queue(
            passive=passive,
            name=queue_name,
            arguments={
                "x-dead-letter-exchange": dlx_name,
                "x-dead-letter-routing-key": dlq_name,
            },
            durable=True,
        )

    @classmethod
    async def declare_scheduler_queue(
        cls,
        channel: AbstractChannel,
        queue_name: str,
        passive: bool = False,
    ) -> AbstractQueue:
        """
        Declare a scheduler queue with simple durable configuration.
        """
        return await channel.declare_queue(
            name=queue_name,
            durable=True,
            passive=passive,
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
        except Exception:
            # Queue might not exist, which is fine
            pass

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
        except Exception:
            # Exchange might not exist, which is fine
            pass
