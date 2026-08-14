# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Iterable
from uuid import uuid4

import redis.asyncio

from jararaca.broker_backend import BrokerBackendLockNotAcquired, MessageBrokerBackend
from jararaca.scheduler.types import DelayedMessageData

logger = logging.getLogger(__name__)

DEFAULT_DELAYED_MESSAGES_BATCH_SIZE = 500
DEFAULT_LOCK_TTL_SECONDS = 30.0
DEFAULT_LOCK_BLOCKING_TIMEOUT_SECONDS = 5.0

_DEQUEUE_DELAYED_MESSAGES_SCRIPT = """
local zset_key = KEYS[1]
local metadata_prefix = ARGV[1]
local max_score = ARGV[2]
local batch_size = tonumber(ARGV[3])

local task_ids = redis.call('ZRANGEBYSCORE', zset_key, '-inf', max_score,
                            'LIMIT', 0, batch_size)
if #task_ids == 0 then
    return {}
end

local payloads = {}
for i = 1, #task_ids do
    local metadata_key = metadata_prefix .. task_ids[i]
    local payload = redis.call('GET', metadata_key)
    if payload then
        payloads[#payloads + 1] = payload
    end
    redis.call('ZREM', zset_key, task_ids[i])
    redis.call('DEL', metadata_key)
end

return payloads
"""
"""
Claims a bounded batch of due delayed messages.

Read and removal happen inside a single Lua invocation, so concurrent beat workers can
never observe the same task id: whoever runs the script first takes the batch. The
batch size bound keeps a backlog of retries from being materialised in memory all at
once, and reading the payloads inside the script removes the round trip per message.

Note: the metadata keys are derived inside the script, which is fine on a standalone
Redis but would be a cross-slot access on Redis Cluster.
"""

_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
"""Releases the lock only if this instance still owns it (compare and delete)."""


class RedisMessageBrokerBackend(MessageBrokerBackend):
    def __init__(
        self,
        url: str,
        *,
        delayed_messages_batch_size: int = DEFAULT_DELAYED_MESSAGES_BATCH_SIZE,
        lock_ttl_seconds: float = DEFAULT_LOCK_TTL_SECONDS,
        lock_blocking_timeout_seconds: float = DEFAULT_LOCK_BLOCKING_TIMEOUT_SECONDS,
    ) -> None:
        self.redis = redis.asyncio.Redis.from_url(url)
        self.last_dispatch_time_key = "last_dispatch_time:{action_name}"
        self.last_execution_time_key = "last_execution_time:{action_name}"
        self.execution_indicator_key = "in_execution:{action_name}:{timestamp}"
        self.execution_indicator_expiration = 60 * 5
        self.delayed_messages_key = "delayed_messages"
        self.delayed_messages_metadata_key = "delayed_messages_metadata:{task_id}"
        self.beat_lock_key = "beat_lock"

        self.delayed_messages_batch_size = delayed_messages_batch_size
        self.lock_ttl_seconds = lock_ttl_seconds
        self.lock_blocking_timeout_seconds = lock_blocking_timeout_seconds

        self._dequeue_delayed_messages_script = self.redis.register_script(
            _DEQUEUE_DELAYED_MESSAGES_SCRIPT
        )
        self._release_lock_script = self.redis.register_script(_RELEASE_LOCK_SCRIPT)

    @asynccontextmanager
    async def lock(self) -> AsyncGenerator[None, None]:
        """
        Acquire the beat lock so that only one beat worker dispatches a given cycle.

        The lock carries a TTL, so a beat worker that dies while holding it does not
        block the others forever; and it is released with a compare and delete, so a
        holder whose TTL already expired cannot release someone else's lock.
        """
        token = str(uuid4()).encode()
        deadline = time.monotonic() + self.lock_blocking_timeout_seconds

        while True:
            acquired = await self.redis.set(
                self.beat_lock_key,
                token,
                nx=True,
                px=int(self.lock_ttl_seconds * 1000),
            )
            if acquired:
                break

            if time.monotonic() >= deadline:
                raise BrokerBackendLockNotAcquired(
                    f"Could not acquire '{self.beat_lock_key}' within "
                    f"{self.lock_blocking_timeout_seconds}s; another instance holds it"
                )

            await asyncio.sleep(0.05)

        try:
            yield
        finally:
            try:
                await self._release_lock_script(keys=[self.beat_lock_key], args=[token])
            except Exception as release_error:
                # The TTL still guarantees the lock is eventually freed.
                logger.warning("Failed to release beat lock: %s", release_error)

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
        task_id = delayed_message.idempotency_key or str(uuid4())
        is_idempotent = delayed_message.idempotency_key is not None

        async with self.redis.pipeline() as pipe:
            metadata_key = self.delayed_messages_metadata_key.format(task_id=task_id)
            serialized = delayed_message.model_dump_json().encode()

            # payload_policy only matters when there may be a pre-existing entry
            if is_idempotent and delayed_message.payload_policy == "ignore":
                pipe.set(metadata_key, serialized, nx=True)
            else:
                pipe.set(metadata_key, serialized)

            # time_policy controls how the dispatch time is updated in the sorted set
            zadd_kwargs: dict[str, bool] = {}
            if is_idempotent:
                if delayed_message.time_policy == "greater":
                    zadd_kwargs["gt"] = True
                elif delayed_message.time_policy == "lesser":
                    zadd_kwargs["lt"] = True
                # "replace" → default zadd behaviour (always add/overwrite)

            pipe.zadd(
                self.delayed_messages_key,
                {task_id: delayed_message.dispatch_time},
                **zadd_kwargs,
            )
            await pipe.execute()

    async def dequeue_next_delayed_messages(
        self, start_timestamp: int
    ) -> Iterable[DelayedMessageData]:
        """
        Claim the next batch of due delayed messages.

        At most `delayed_messages_batch_size` messages are returned per call. A backlog
        larger than that is drained across successive beat cycles instead of being read
        into memory and republished in one burst, which is what a retry storm produces.
        """
        metadata_prefix = self.delayed_messages_metadata_key.format(task_id="")

        payloads: list[Any] = await self._dequeue_delayed_messages_script(
            keys=[self.delayed_messages_key],
            args=[
                metadata_prefix,
                start_timestamp,
                self.delayed_messages_batch_size,
            ],
        )

        if not payloads:
            return []

        delayed_messages: list[DelayedMessageData] = []

        for payload in payloads:
            raw = payload.decode() if isinstance(payload, bytes) else str(payload)
            try:
                delayed_messages.append(DelayedMessageData.model_validate_json(raw))
            except Exception:
                logger.error("Error parsing delayed message: %s", raw)
                continue

        return delayed_messages

    async def dispose(self) -> None:
        """
        Dispose of the message broker backend.
        This is used to close the connection to the message broker.
        """
        await self.redis.close()
