import asyncio
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from jararaca.presentation.websocket.websocket_interceptor import (
    BroadcastFunc,
    SendFunc,
    WebSocketConnectionBackend,
)


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
    ) -> None:

        self.redis = conn
        self.broadcast_pubsub_channel = broadcast_pubsub_channel
        self.send_pubsub_channel = send_pubsub_channel

        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task[Any]] = set()

        self.consume_broadcast_timeout = consume_broadcast_timeout
        self.consume_send_timeout = consume_send_timeout

    async def broadcast(self, message: bytes) -> None:
        await self.redis.publish(
            self.broadcast_pubsub_channel,
            BroadcastMessage.from_message(message).encode(),
        )

    async def send(self, rooms: list[str], message: bytes) -> None:
        await self.redis.publish(
            self.send_pubsub_channel,
            SendToRoomsMessage.from_message(rooms, message).encode(),
        )

    def configure(
        self, broadcast: BroadcastFunc, send: SendFunc, shutdown_event: asyncio.Event
    ) -> None:

        broadcast_task = asyncio.get_event_loop().create_task(
            self.consume_broadcast(broadcast, shutdown_event)
        )

        send_task = asyncio.get_event_loop().create_task(
            self.consume_send(send, shutdown_event)
        )

        self.tasks.add(broadcast_task)
        self.tasks.add(send_task)

    async def consume_broadcast(
        self, broadcast: BroadcastFunc, shutdown_event: asyncio.Event
    ) -> None:

        async with self.redis.pubsub() as pubsub:
            await pubsub.subscribe(self.broadcast_pubsub_channel)

            while not shutdown_event.is_set():
                message: dict[str, Any] | None = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=self.consume_broadcast_timeout,
                )

                if message is None:
                    continue

                broadcast_message = BroadcastMessage.decode(message["data"])

                async with self.lock:
                    task = asyncio.get_event_loop().create_task(
                        broadcast(message=broadcast_message.message)
                    )

                    self.tasks.add(task)

                    task.add_done_callback(self.tasks.discard)

    async def consume_send(self, send: SendFunc, shutdown_event: asyncio.Event) -> None:

        async with self.redis.pubsub() as pubsub:
            await pubsub.subscribe(self.send_pubsub_channel)

            while not shutdown_event.is_set():

                message: dict[str, Any] | None = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=self.consume_send_timeout
                )

                if message is None:
                    continue

                send_message = SendToRoomsMessage.decode(message["data"])

                async with self.lock:

                    task = asyncio.get_event_loop().create_task(
                        send(send_message.rooms, send_message.message)
                    )

                    self.tasks.add(task)

                    task.add_done_callback(self.tasks.discard)

    async def shutdown(self) -> None:
        async with self.lock:

            await asyncio.gather(*self.tasks, return_exceptions=True)

            await self.redis.close()
