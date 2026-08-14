# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the message bus retry scheduling, its attempt budget and the shutdown-aware
wait helper.
"""

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jararaca.messagebus.message import Message
from jararaca.messagebus.worker import (
    AioPikaMessageBusController,
    AioPikaMicroserviceConsumer,
    AioPikaWorkerConfig,
    MessageHandlerCallback,
)
from jararaca.utils.retry import RetryPolicy


class SampleTaskMessage(Message):
    MESSAGE_TOPIC = "sample.task"


def build_callback(
    max_retries: int = 3,
    nack_on_exception: bool = True,
) -> tuple[MessageHandlerCallback, list[Any]]:
    """Builds a callback wired to a fake broker backend that records what was scheduled."""
    scheduled: list[Any] = []

    broker_backend = MagicMock()
    broker_backend.enqueue_delayed_message = AsyncMock(
        side_effect=lambda delayed_message: scheduled.append(delayed_message)
    )

    config = AioPikaWorkerConfig(
        url="amqp://guest:guest@localhost:5672/?exchange=main&prefetch_count=1",
        exchange="main",
        default_prefetch_count=1,
        shared_default_channel=False,
        prefetch_by_channel_id={},
        consumer_retry_policy=RetryPolicy(
            max_retries=max_retries,
            initial_delay=1.0,
            max_delay=10.0,
            backoff_factor=2.0,
        ),
    )

    consumer = AioPikaMicroserviceConsumer(
        broker_backend=broker_backend,
        config=config,
        message_handler_set=set(),
        scheduled_actions=set(),
        uow_context_provider=MagicMock(),
    )

    message_handler = MagicMock()
    message_handler.spec.retry_config = None
    message_handler.spec.nack_on_exception = nack_on_exception
    message_handler.message_type = SampleTaskMessage

    callback = MessageHandlerCallback(
        consumer=consumer,
        queue_name="sample-queue",
        routing_key="sample.task",
        message_handler=message_handler,
    )

    return callback, scheduled


def build_message(retry_count: int = 0) -> MagicMock:
    message = MagicMock()
    message.message_id = "message-id"
    message.body = b"payload"
    message.headers = {"x-retry-count": retry_count} if retry_count else {}
    message.content_type = None
    message.content_encoding = None
    message.ack = AsyncMock()
    message.reject = AsyncMock()
    return message


def build_controller(
    callback: MessageHandlerCallback, message: MagicMock
) -> AioPikaMessageBusController:
    controller = AioPikaMessageBusController(message)
    # Bypass the frame walking used to locate the callback at runtime.
    controller._callback = callback
    return controller


class TestScheduleRetry:

    async def test_increments_the_attempt_counter(self) -> None:
        callback, scheduled = build_callback()
        message = build_message(retry_count=2)

        assert await callback.schedule_retry(message, retry_count=2) is True

        assert len(scheduled) == 1
        assert scheduled[0].headers["x-retry-count"] == 3

    async def test_acks_the_original_once_the_copy_is_scheduled(self) -> None:
        callback, scheduled = build_callback()
        message = build_message()

        await callback.schedule_retry(message, retry_count=0)

        assert message.ack.await_count == 1
        assert scheduled[0].target_queue == "sample-queue"
        assert scheduled[0].payload == b"payload"

    async def test_dead_letters_once_the_budget_is_exhausted(self) -> None:
        callback, scheduled = build_callback(max_retries=3)
        message = build_message(retry_count=3)

        assert await callback.schedule_retry(message, retry_count=3) is False

        assert scheduled == []
        message.reject.assert_awaited_once_with(requeue=False)

    async def test_explicit_delay_wins_over_the_backoff(self) -> None:
        callback, scheduled = build_callback()
        message = build_message()

        before = time.time()
        await callback.schedule_retry(message, retry_count=0, delay=42)

        assert scheduled[0].dispatch_time == pytest.approx(before + 42, abs=2)

    async def test_backoff_is_used_when_no_delay_is_given(self) -> None:
        callback, scheduled = build_callback(max_retries=10)
        message = build_message(retry_count=3)

        before = time.time()
        await callback.schedule_retry(message, retry_count=3)

        # initial_delay 1.0 * backoff 2.0 ** 3 == 8s, +- 25% jitter.
        assert scheduled[0].dispatch_time == pytest.approx(before + 8, abs=3)

    async def test_records_the_failure_reason_when_there_is_one(self) -> None:
        callback, scheduled = build_callback()
        message = build_message()

        await callback.schedule_retry(
            message, retry_count=0, exception=RuntimeError("boom")
        )

        assert scheduled[0].headers["x-last-error"] == "boom"

    async def test_requeues_when_the_backend_cannot_take_the_retry(self) -> None:
        callback, scheduled = build_callback()
        callback.consumer.broker_backend.enqueue_delayed_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("redis down")
        )
        message = build_message()

        assert await callback.schedule_retry(message, retry_count=0) is False

        message.reject.assert_awaited_once_with(requeue=True)
        assert message.ack.await_count == 0

    async def test_preserves_unrelated_headers(self) -> None:
        callback, scheduled = build_callback()
        message = build_message()
        message.headers = {"x-tenant": "acme", "traceparent": "00-abc-def-01"}

        await callback.schedule_retry(message, retry_count=0)

        assert scheduled[0].headers["x-tenant"] == "acme"
        assert scheduled[0].headers["traceparent"] == "00-abc-def-01"


class TestControllerRetryBudget:
    """`retry()` and `retry_later()` must count against the same budget."""

    async def test_retry_increments_the_counter(self) -> None:
        callback, scheduled = build_callback()
        message = build_message(retry_count=1)

        await build_controller(callback, message).retry(delay=5)

        assert scheduled[0].headers["x-retry-count"] == 2

    async def test_retry_dead_letters_at_the_budget(self) -> None:
        callback, scheduled = build_callback(max_retries=3)
        message = build_message(retry_count=3)

        await build_controller(callback, message).retry(delay=5)

        assert scheduled == []
        message.reject.assert_awaited_once_with(requeue=False)

    async def test_retry_later_increments_the_counter(self) -> None:
        callback, scheduled = build_callback()
        message = build_message(retry_count=1)

        await build_controller(callback, message).retry_later(60)

        assert scheduled[0].headers["x-retry-count"] == 2

    async def test_retry_later_dead_letters_at_the_budget(self) -> None:
        callback, scheduled = build_callback(max_retries=3)
        message = build_message(retry_count=3)

        await build_controller(callback, message).retry_later(60)

        assert scheduled == []
        message.reject.assert_awaited_once_with(requeue=False)


class TestWaitDelayOrShutdown:

    async def test_does_not_leak_tasks(self) -> None:
        callback, _ = build_callback()
        consumer = callback.consumer

        before = len(asyncio.all_tasks())
        for _ in range(20):
            await consumer._wait_delay_or_shutdown(0.001)
        await asyncio.sleep(0.01)

        assert len(asyncio.all_tasks()) == before

    async def test_returns_immediately_once_shutdown_is_requested(self) -> None:
        callback, _ = build_callback()
        consumer = callback.consumer
        consumer.shutdown_event.set()

        started = time.monotonic()
        await consumer._wait_delay_or_shutdown(30)

        assert time.monotonic() - started < 0.5

    async def test_waits_for_the_delay_when_no_shutdown_happens(self) -> None:
        callback, _ = build_callback()
        consumer = callback.consumer

        started = time.monotonic()
        await consumer._wait_delay_or_shutdown(0.15)

        assert time.monotonic() - started >= 0.1
