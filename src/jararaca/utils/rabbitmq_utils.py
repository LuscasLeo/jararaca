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
        await channel.set_qos(prefetch_count=1)
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
        await channel.set_qos(prefetch_count=1)
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
        await channel.set_qos(prefetch_count=1)
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
        await channel.set_qos(prefetch_count=1)
        return await channel.declare_queue(
            queue_name,
            passive=passive,
            durable=True,
            arguments={
                "x-dead-letter-exchange": cls.DEAD_LETTER_EXCHANGE,
                "x-dead-letter-routing-key": cls.DEAD_LETTER_EXCHANGE,
            },
        )
