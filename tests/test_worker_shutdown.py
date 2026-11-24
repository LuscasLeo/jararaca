# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Worker shutdown: channels and connection are closed for real, scheduled actions
keep their documented at-most-once delivery."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from jararaca.messagebus.worker import AioPikaMicroserviceConsumer, AioPikaWorkerConfig
from jararaca.utils.rabbitmq_utils import RabbitmqUtils
from jararaca.utils.retry import RetryPolicy


def build_consumer(
    scheduled_actions: set[Any] | None = None,
) -> AioPikaMicroserviceConsumer:
    config = AioPikaWorkerConfig(
        url="amqp://guest:guest@localhost:5672/?exchange=main&prefetch_count=1",
        exchange="main",
        default_prefetch_count=1,
        shared_default_channel=False,
        prefetch_by_channel_id={},
        consumer_retry_policy=RetryPolicy(
            max_retries=0, initial_delay=0.01, max_delay=0.01, backoff_factor=1.0
        ),
    )
    return AioPikaMicroserviceConsumer(
        broker_backend=MagicMock(),
        config=config,
        message_handler_set=set(),
        scheduled_actions=scheduled_actions or set(),
        uow_context_provider=MagicMock(),
    )


def fake_amqp_object(is_closed: bool = False) -> MagicMock:
    obj = MagicMock()
    obj.is_closed = is_closed
    obj.close = AsyncMock()
    return obj


class TestCloseChannelsAndConnection:
    async def test_closes_every_open_channel_and_then_the_connection(self) -> None:
        consumer = build_consumer()
        order: list[str] = []
        first, second = fake_amqp_object(), fake_amqp_object()
        first.close.side_effect = lambda: order.append("channel:first")
        second.close.side_effect = lambda: order.append("channel:second")
        connection = fake_amqp_object()
        connection.close.side_effect = lambda: order.append("connection")
        consumer.channels = {"first": first, "second": second}
        consumer.connection = connection

        await consumer.close_channels_and_connection()

        assert order == ["channel:first", "channel:second", "connection"]
        assert consumer.channels == {}
        assert consumer.connection is None

    async def test_skips_channels_and_connection_already_closed(self) -> None:
        consumer = build_consumer()
        closed_channel = fake_amqp_object(is_closed=True)
        closed_connection = fake_amqp_object(is_closed=True)
        consumer.channels = {"closed": closed_channel}
        consumer.connection = closed_connection

        await consumer.close_channels_and_connection()

        closed_channel.close.assert_not_awaited()
        closed_connection.close.assert_not_awaited()

    async def test_a_failing_channel_close_does_not_keep_the_connection_open(
        self,
    ) -> None:
        consumer = build_consumer()
        broken = fake_amqp_object()
        broken.close.side_effect = RuntimeError("channel already gone")
        connection = fake_amqp_object()
        consumer.channels = {"broken": broken}
        consumer.connection = connection

        await consumer.close_channels_and_connection()

        connection.close.assert_awaited_once()

    async def test_an_unresponsive_broker_does_not_hang_the_shutdown(self) -> None:
        consumer = build_consumer()
        hanging = fake_amqp_object()

        async def never_returns() -> None:
            await asyncio.Event().wait()

        hanging.close = AsyncMock(side_effect=never_returns)
        connection = fake_amqp_object()
        consumer.channels = {"hanging": hanging}
        consumer.connection = connection

        with patch("jararaca.messagebus.worker.CLOSE_TIMEOUT_SECONDS", 0.05):
            await asyncio.wait_for(consumer.close_channels_and_connection(), 1)

        connection.close.assert_awaited_once()

    async def test_is_idempotent(self) -> None:
        consumer = build_consumer()
        connection = fake_amqp_object()
        consumer.channels = {"only": fake_amqp_object()}
        consumer.connection = connection

        await consumer.close_channels_and_connection()
        await consumer.close_channels_and_connection()

        connection.close.assert_awaited_once()

    async def test_close_drains_before_closing(self) -> None:
        consumer = build_consumer()
        order: list[str] = []
        connection = fake_amqp_object()
        connection.close.side_effect = lambda: order.append("connection")
        consumer.connection = connection

        async def in_flight_handler() -> None:
            await asyncio.sleep(0.05)
            order.append("handler done")

        consumer.tasks.add(asyncio.create_task(in_flight_handler()))

        await consumer.close()

        assert order == ["handler done", "connection"]


class TestScheduledActionDelivery:
    async def test_scheduled_action_consumes_with_no_ack(self) -> None:
        """Scheduled actions are documented as at-most-once (docs/configuration.md)."""
        scheduled_action = MagicMock()
        scheduled_action.callable.__module__ = "app.jobs"
        scheduled_action.callable.__qualname__ = "Jobs.tick"
        scheduled_action.spec.channel_id = None
        consumer = build_consumer(scheduled_actions={scheduled_action})

        queue = MagicMock()
        queue.consume = AsyncMock(return_value="consumer-tag")
        queue.cancel = AsyncMock()
        consumer.retrieve_channel = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]

        with patch.object(RabbitmqUtils, "get_queue", AsyncMock(return_value=queue)):
            assert await consumer._setup_scheduled_action_consumer(scheduled_action)

        assert queue.consume.await_args is not None
        assert queue.consume.await_args.kwargs["no_ack"] is True
        assert consumer.active_consumers == {
            "app.jobs.Jobs.tick": (queue, "consumer-tag")
        }
