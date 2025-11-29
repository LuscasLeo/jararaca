# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from jararaca.presentation.websocket.websocket_interceptor import (
    BroadcastFunc,
    SendFunc,
    WebSocketConnectionBackend,
)

logger = logging.getLogger(__name__)


@dataclass
class BroadcastMessage:
    message: bytes

    def encode(self) -> bytes:
        return self.message

    @staticmethod
    def decode(data: bytes) -> "BroadcastMessage":
        return BroadcastMessage(message=data)

    @staticmethod
    def from_message(message: bytes) -> "BroadcastMessage":
        return BroadcastMessage(message=message)


@dataclass
class SendToRoomsMessage:
    rooms: list[str]
    message: bytes

    def encode(self) -> bytes:
        return b"\x00".join([b",".join([a.encode() for a in self.rooms]), self.message])

    @staticmethod
    def decode(data: bytes) -> "SendToRoomsMessage":
        rooms, message = data.split(b"\x00")
        return SendToRoomsMessage(
            rooms=[a.decode() for a in rooms.split(b",")], message=message
        )

    @staticmethod
    def from_message(rooms: list[str], message: bytes) -> "SendToRoomsMessage":
        return SendToRoomsMessage(rooms=rooms, message=message)


class RedisWebSocketConnectionBackend(WebSocketConnectionBackend):
    def __init__(
        self,
        conn: "Redis[bytes]",
        broadcast_pubsub_channel: str,
        send_pubsub_channel: str,
        consume_broadcast_timeout: int = 1,
        consume_send_timeout: int = 1,
        retry_delay: float = 5.0,
        max_concurrent_tasks: int = 1000,
    ) -> None:

        self.redis = conn
        self.broadcast_pubsub_channel = broadcast_pubsub_channel
        self.send_pubsub_channel = send_pubsub_channel

        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.max_concurrent_tasks = max_concurrent_tasks
        self.task_semaphore = asyncio.Semaphore(max_concurrent_tasks)

        self.consume_broadcast_timeout = consume_broadcast_timeout
        self.consume_send_timeout = consume_send_timeout
        self.retry_delay = retry_delay
        self.__shutdown_event: asyncio.Event | None = None

        self.__send_func: SendFunc | None = None
        self.__broadcast_func: BroadcastFunc | None = None

    @property
    def shutdown_event(self) -> asyncio.Event:
        if self.__shutdown_event is None:
            raise RuntimeError(
                "Shutdown event is not set. Please configure the backend before using it."
            )
        return self.__shutdown_event

    @property
    def send_func(self) -> SendFunc:
        if self.__send_func is None:
            raise RuntimeError(
                "Send function is not set. Please configure the backend before using it."
            )
        return self.__send_func

    @property
    def broadcast_func(self) -> BroadcastFunc:
        if self.__broadcast_func is None:
            raise RuntimeError(
                "Broadcast function is not set. Please configure the backend before using it."
            )
        return self.__broadcast_func

    async def broadcast(self, message: bytes) -> None:
        try:
            await self.redis.publish(
                self.broadcast_pubsub_channel,
                BroadcastMessage.from_message(message).encode(),
            )
        except Exception as e:
            logger.error(
                "Failed to publish broadcast message to Redis: %s", e, exc_info=True
            )
            raise

    async def send(self, rooms: list[str], message: bytes) -> None:
        try:
            await self.redis.publish(
                self.send_pubsub_channel,
                SendToRoomsMessage.from_message(rooms, message).encode(),
            )
        except Exception as e:
            logger.error(
                "Failed to publish send message to Redis: %s", e, exc_info=True
            )
            raise

    def configure(
        self, broadcast: BroadcastFunc, send: SendFunc, shutdown_event: asyncio.Event
    ) -> None:
        if self.__shutdown_event is not None:
            raise RuntimeError("Backend is already configured.")
        self.__shutdown_event = shutdown_event
        self.__send_func = send
        self.__broadcast_func = broadcast
        self.setup_send_consumer()
        self.setup_broadcast_consumer()

    def setup_send_consumer(self) -> None:

        send_task = asyncio.get_event_loop().create_task(
            self.consume_send(self.send_func, self.shutdown_event)
        )

        # Use lock when modifying tasks set to prevent race conditions
        async def add_task() -> None:
            async with self.lock:
                self.tasks.add(send_task)

        asyncio.get_event_loop().create_task(add_task())
        send_task.add_done_callback(self.handle_send_task_done)

    def setup_broadcast_consumer(self) -> None:

        broadcast_task = asyncio.get_event_loop().create_task(
            self.consume_broadcast(self.broadcast_func, self.shutdown_event)
        )

        # Use lock when modifying tasks set to prevent race conditions
        async def add_task() -> None:
            async with self.lock:
                self.tasks.add(broadcast_task)

        asyncio.get_event_loop().create_task(add_task())

        broadcast_task.add_done_callback(self.handle_broadcast_task_done)

    def handle_broadcast_task_done(self, task: asyncio.Task[Any]) -> None:
        # Remove task from set safely with lock
        async def remove_task() -> None:
            async with self.lock:
                self.tasks.discard(task)

        asyncio.get_event_loop().create_task(remove_task())

        if task.cancelled():
            logger.warning("Broadcast task was cancelled.")
        elif task.exception() is not None:
            logger.exception(
                "Broadcast task raised an exception:", exc_info=task.exception()
            )
        else:
            logger.warning("Broadcast task somehow completed successfully.")

        if not self.shutdown_event.is_set():
            logger.warning(
                "Broadcast task completed, but shutdown event is not set. This is unexpected."
            )
            # Add delay before retrying to avoid excessive CPU usage
            asyncio.get_event_loop().create_task(
                self._retry_broadcast_consumer_with_delay()
            )

    def handle_send_task_done(self, task: asyncio.Task[Any]) -> None:
        # Remove task from set safely with lock
        async def remove_task() -> None:
            async with self.lock:
                self.tasks.discard(task)

        asyncio.get_event_loop().create_task(remove_task())

        if task.cancelled():
            logger.warning("Send task was cancelled.")
        elif task.exception() is not None:
            logger.exception(
                "Send task raised an exception:", exc_info=task.exception()
            )
        else:
            logger.warning("Send task somehow completed successfully.")

        if not self.shutdown_event.is_set():
            logger.warning(
                "Send task completed, but shutdown event is not set. This is unexpected."
            )
            # Add delay before retrying to avoid excessive CPU usage
            asyncio.get_event_loop().create_task(self._retry_send_consumer_with_delay())

    async def _retry_broadcast_consumer_with_delay(self) -> None:
        """Retry setting up broadcast consumer after a delay to avoid excessive CPU usage."""
        logger.warning(
            "Waiting %s seconds before retrying broadcast consumer...", self.retry_delay
        )
        await asyncio.sleep(self.retry_delay)

        if not self.shutdown_event.is_set():
            logger.warning("Retrying broadcast consumer setup...")
            self.setup_broadcast_consumer()

    async def _retry_send_consumer_with_delay(self) -> None:
        """Retry setting up send consumer after a delay to avoid excessive CPU usage."""
        logger.warning(
            "Waiting %s seconds before retrying send consumer...", self.retry_delay
        )
        await asyncio.sleep(self.retry_delay)

        if not self.shutdown_event.is_set():
            logger.warning("Retrying send consumer setup...")
            self.setup_send_consumer()

    async def consume_broadcast(
        self, broadcast: BroadcastFunc, shutdown_event: asyncio.Event
    ) -> None:
        logger.debug("Starting broadcast consumer...")
        try:
            # Validate Redis connection before starting
            try:
                await self.redis.ping()
                logger.debug("Redis connection validated for broadcast consumer")
            except Exception as e:
                logger.error("Redis connection validation failed: %s", e, exc_info=True)
                raise

            async with self.redis.pubsub() as pubsub:
                await pubsub.subscribe(self.broadcast_pubsub_channel)
                logger.debug(
                    "Subscribed to broadcast channel: %s", self.broadcast_pubsub_channel
                )

                while not shutdown_event.is_set():
                    message: dict[str, Any] | None = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=self.consume_broadcast_timeout,
                    )

                    if message is None:
                        continue

                    broadcast_message = BroadcastMessage.decode(message["data"])

                    # Use semaphore for backpressure control
                    acquired = False
                    try:
                        await self.task_semaphore.acquire()
                        acquired = True

                        async def broadcast_with_cleanup(msg: bytes) -> None:
                            try:
                                await broadcast(message=msg)
                            finally:
                                self.task_semaphore.release()

                        async with self.lock:
                            task = asyncio.get_event_loop().create_task(
                                broadcast_with_cleanup(broadcast_message.message)
                            )

                            self.tasks.add(task)

                            task.add_done_callback(self.tasks.discard)
                    except Exception as e:
                        # Release semaphore if we acquired it but failed to create task
                        if acquired:
                            self.task_semaphore.release()
                        logger.error(
                            "Error processing broadcast message: %s", e, exc_info=True
                        )
                        # Continue processing other messages
                        continue
        except Exception as e:
            logger.error(
                "Fatal error in broadcast consumer, will retry: %s", e, exc_info=True
            )
            raise

    async def consume_send(self, send: SendFunc, shutdown_event: asyncio.Event) -> None:
        logger.debug("Starting send consumer...")
        try:
            # Validate Redis connection before starting
            try:
                await self.redis.ping()
                logger.debug("Redis connection validated for send consumer")
            except Exception as e:
                logger.error("Redis connection validation failed: %s", e, exc_info=True)
                raise

            async with self.redis.pubsub() as pubsub:
                await pubsub.subscribe(self.send_pubsub_channel)
                logger.debug("Subscribed to send channel: %s", self.send_pubsub_channel)

                while not shutdown_event.is_set():
                    message: dict[str, Any] | None = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=self.consume_send_timeout,
                    )

                    if message is None:
                        continue

                    send_message = SendToRoomsMessage.decode(message["data"])

                    # Use semaphore for backpressure control
                    acquired = False
                    try:
                        await self.task_semaphore.acquire()
                        acquired = True

                        async def send_with_cleanup(
                            rooms: list[str], msg: bytes
                        ) -> None:
                            try:
                                await send(rooms, msg)
                            finally:
                                self.task_semaphore.release()

                        async with self.lock:

                            task = asyncio.get_event_loop().create_task(
                                send_with_cleanup(
                                    send_message.rooms, send_message.message
                                )
                            )

                            self.tasks.add(task)

                            task.add_done_callback(self.tasks.discard)
                    except Exception as e:
                        # Release semaphore if we acquired it but failed to create task
                        if acquired:
                            self.task_semaphore.release()
                        logger.error(
                            "Error processing send message: %s", e, exc_info=True
                        )
                        # Continue processing other messages
                        continue
        except Exception as e:
            logger.error(
                "Fatal error in send consumer, will retry: %s", e, exc_info=True
            )
            raise

    async def shutdown(self) -> None:
        async with self.lock:

            await asyncio.gather(*self.tasks, return_exceptions=True)

            await self.redis.close()
