import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Iterable
from uuid import uuid4

import redis.asyncio

from jararaca.broker_backend import MessageBrokerBackend
from jararaca.scheduler.types import DelayedMessageData

logger = logging.getLogger(__name__)


class RedisMessageBrokerBackend(MessageBrokerBackend):
    def __init__(self, url: str) -> None:
        self.redis = redis.asyncio.Redis.from_url(url)
        self.last_dispatch_time_key = "last_dispatch_time:{action_name}"
        self.last_execution_time_key = "last_execution_time:{action_name}"
        self.execution_indicator_key = "in_execution:{action_name}:{timestamp}"
        self.execution_indicator_expiration = 60 * 5
        self.delayed_messages_key = "delayed_messages"
        self.delayed_messages_metadata_key = "delayed_messages_metadata:{task_id}"

    @asynccontextmanager
    async def lock(self) -> AsyncGenerator[None, None]:
        yield

    async def get_last_dispatch_time(self, action_name: str) -> int | None:

        key = self.last_dispatch_time_key.format(action_name=action_name)
        last_execution_time = await self.redis.get(key)
        if last_execution_time is None:
            return None
        return int(last_execution_time)

    async def set_last_dispatch_time(self, action_name: str, timestamp: int) -> None:
        key = self.last_dispatch_time_key.format(action_name=action_name)
        await self.redis.set(key, timestamp)

    async def get_last_execution_time(self, action_name: str) -> int | None:
        key = self.last_execution_time_key.format(action_name=action_name)
        last_execution_time = await self.redis.get(key)
        if last_execution_time is None:
            return None
        return int(last_execution_time)

    async def set_last_execution_time(self, action_name: str, timestamp: int) -> None:
        key = self.last_execution_time_key.format(action_name=action_name)
        await self.redis.set(key, timestamp)

    async def get_in_execution_count(self, action_name: str) -> int:
        key = self.execution_indicator_key.format(
            action_name=action_name, timestamp="*"
        )
        in_execution_count = await self.redis.keys(key)
        if in_execution_count is None:
            return 0

        return len(in_execution_count)

    @asynccontextmanager
    async def in_execution(self, action_name: str) -> AsyncGenerator[None, None]:
        """
        Acquire a lock for the scheduled action.
        This is used to ensure that only one instance of the scheduled action is running at a time.
        """
        key = self.execution_indicator_key.format(
            action_name=action_name, timestamp=int(time.time())
        )
        await self.redis.set(key, 1, ex=self.execution_indicator_expiration)
        try:
            yield
        finally:
            await self.redis.delete(key)

    async def enqueue_delayed_message(
        self, delayed_message: DelayedMessageData
    ) -> None:
        """
        Enqueue a delayed message to the message broker.
        This is used to trigger the scheduled action.
        """
        task_id = str(uuid4())
        async with self.redis.pipeline() as pipe:
            pipe.set(
                self.delayed_messages_metadata_key.format(task_id=task_id),
                delayed_message.model_dump_json().encode(),
            )
            pipe.zadd(
                self.delayed_messages_key,
                {task_id: delayed_message.dispatch_time},
                nx=True,
            )
            await pipe.execute()

    async def dequeue_next_delayed_messages(
        self, start_timestamp: int
    ) -> Iterable[DelayedMessageData]:
        """
        Dequeue the next delayed messages from the message broker.
        This is used to trigger the scheduled action.
        """
        tasks_ids = await self.redis.zrangebyscore(
            name=self.delayed_messages_key,
            max=start_timestamp,
            min="-inf",
            withscores=False,
        )

        if not tasks_ids:
            return []

        tasks_bytes_data: list[bytes] = []

        for task_id_bytes in tasks_ids:
            metadata = await self.redis.get(
                self.delayed_messages_metadata_key.format(
                    task_id=task_id_bytes.decode()
                )
            )
            if metadata is None:
                logger.warning(
                    f"Delayed message metadata not found for task_id: {task_id_bytes.decode()}"
                )

                continue

            tasks_bytes_data.append(metadata)

        async with self.redis.pipeline() as pipe:
            for task_id_bytes in tasks_ids:
                pipe.zrem(self.delayed_messages_key, task_id_bytes.decode())
                pipe.delete(
                    self.delayed_messages_metadata_key.format(
                        task_id=task_id_bytes.decode()
                    )
                )
            await pipe.execute()

        delayed_messages: list[DelayedMessageData] = []

        for task_bytes_data in tasks_bytes_data:
            try:
                delayed_message = DelayedMessageData.model_validate_json(
                    task_bytes_data.decode()
                )
                delayed_messages.append(delayed_message)
            except Exception:
                logger.error(
                    f"Error parsing delayed message: {task_bytes_data.decode()}"
                )
                continue

        return delayed_messages

    async def dispose(self) -> None:
        """
        Dispose of the message broker backend.
        This is used to close the connection to the message broker.
        """
        await self.redis.close()
