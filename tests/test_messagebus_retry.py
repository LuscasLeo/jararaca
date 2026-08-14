# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the message bus retry scheduling, its attempt budget and the shutdown-aware
wait helper.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aio_pika.abc import AbstractIncomingMessage

from jararaca.messagebus import bus_message_controller as bus_message_controller_hooks
from jararaca.messagebus.bus_message_controller import (
    MessageDisposed,
    provide_bus_message_controller,
)
from jararaca.messagebus.decorators import MessageHandler
from jararaca.messagebus.message import Message
from jararaca.messagebus.worker import (
    SLOT_WAIT_WARNING_THRESHOLD_SECONDS,
    AioPikaMessageBusController,
    AioPikaMicroserviceConsumer,
    AioPikaWorkerConfig,
    MessageHandlerCallback,
)
from jararaca.microservice import AppTransactionContext, MessageBusTransactionData
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
    message.nack = AsyncMock()
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

        with pytest.raises(MessageDisposed):
            await build_controller(callback, message).retry(delay=5)

        assert scheduled[0].headers["x-retry-count"] == 2

    async def test_retry_dead_letters_at_the_budget(self) -> None:
        callback, scheduled = build_callback(max_retries=3)
        message = build_message(retry_count=3)

        with pytest.raises(MessageDisposed):
            await build_controller(callback, message).retry(delay=5)

        assert scheduled == []
        message.reject.assert_awaited_once_with(requeue=False)

    async def test_retry_later_increments_the_counter(self) -> None:
        callback, scheduled = build_callback()
        message = build_message(retry_count=1)

        with pytest.raises(MessageDisposed):
            await build_controller(callback, message).retry_later(60)

        assert scheduled[0].headers["x-retry-count"] == 2

    async def test_retry_later_dead_letters_at_the_budget(self) -> None:
        callback, scheduled = build_callback(max_retries=3)
        message = build_message(retry_count=3)

        with pytest.raises(MessageDisposed):
            await build_controller(callback, message).retry_later(60)

        assert scheduled == []
        message.reject.assert_awaited_once_with(requeue=False)


class TestDisposition:
    """
    A handler that hands the message back must stop there: whatever runs afterwards
    races the redelivery of that very message.
    """

    @pytest.mark.parametrize(
        "disposition,invoke",
        [
            ("retry", lambda controller: controller.retry(delay=5)),
            ("retry", lambda controller: controller.retry_later(60)),
            ("nack", lambda controller: controller.nack()),
            ("reject", lambda controller: controller.reject()),
        ],
    )
    async def test_handing_the_message_back_unwinds_the_handler(
        self,
        disposition: str,
        invoke: Any,
    ) -> None:
        callback, _ = build_callback()
        message = build_message()
        controller = build_controller(callback, message)

        with pytest.raises(MessageDisposed) as raised:
            await invoke(controller)

        assert raised.value.disposition == disposition
        assert controller.disposition == disposition

    async def test_ack_settles_without_unwinding_the_handler(self) -> None:
        callback, _ = build_callback()
        message = build_message()
        controller = build_controller(callback, message)

        # Acking is safe to continue after: the message is settled, so nothing can
        # be redelivered to race the rest of the handler.
        await controller.ack()

        assert controller.disposition == "ack"
        assert message.ack.await_count == 1

    async def test_is_not_swallowed_by_a_broad_except_clause(self) -> None:
        callback, _ = build_callback()
        message = build_message()
        controller = build_controller(callback, message)

        swallowed = False

        with pytest.raises(MessageDisposed):
            try:
                await controller.retry(delay=5)
            except Exception:  # noqa: BLE001 - deliberately broad, as user code is
                swallowed = True

        assert not swallowed

    async def test_module_level_hook_propagates_the_disposition(self) -> None:
        callback, _ = build_callback()
        message = build_message()
        controller = build_controller(callback, message)

        with provide_bus_message_controller(controller):
            with pytest.raises(MessageDisposed):
                await bus_message_controller_hooks.retry()

    async def test_undecided_message_reports_no_disposition(self) -> None:
        callback, _ = build_callback()
        controller = build_controller(callback, build_message())

        assert controller.disposition is None


class TestProcessingSlots:
    """
    `prefetch_count` stops bounding concurrency as soon as a message is settled before
    its handler returns, so the worker enforces the bound itself.
    """

    async def test_concurrent_handlers_stay_within_the_prefetch_count(self) -> None:
        callback, _ = build_callback()
        callback.consumer.config.default_prefetch_count = 3

        live = 0
        peak = 0

        async def slow_handler(
            aio_pika_message: AbstractIncomingMessage,
            slot_wait_seconds: float = 0.0,
        ) -> None:
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            live -= 1

        callback.handle_message = slow_handler  # type: ignore[method-assign]

        await asyncio.gather(
            *[callback.run_within_processing_slot(MagicMock()) for _ in range(30)]
        )

        assert peak == 3

    async def test_per_channel_prefetch_overrides_are_honoured(self) -> None:
        callback, _ = build_callback()
        consumer = callback.consumer
        consumer.config.default_prefetch_count = 4
        consumer.config.prefetch_by_channel_id = {"heavy": 1}

        heavy = consumer.get_processing_slot("heavy")
        other = consumer.get_processing_slot("other")

        assert heavy is not None and heavy._value == 1
        assert other is not None and other._value == 4

    async def test_slot_is_released_when_the_handler_raises(self) -> None:
        callback, _ = build_callback()
        callback.consumer.config.default_prefetch_count = 2

        async def failing_handler(
            aio_pika_message: AbstractIncomingMessage,
            slot_wait_seconds: float = 0.0,
        ) -> None:
            raise RuntimeError("boom")

        callback.handle_message = failing_handler  # type: ignore[method-assign]

        for _ in range(5):
            with pytest.raises(RuntimeError):
                await callback.run_within_processing_slot(MagicMock())

        slot = callback.consumer.get_processing_slot(callback.channel_id)
        assert slot is not None and slot._value == 2

    async def test_zero_prefetch_means_unlimited(self) -> None:
        """AMQP defines prefetch_count=0 as no limit; the worker must agree."""
        callback, _ = build_callback()
        callback.consumer.config.default_prefetch_count = 0

        assert callback.consumer.get_processing_slot(callback.channel_id) is None

        live = 0
        peak = 0

        async def slow_handler(
            aio_pika_message: AbstractIncomingMessage,
            slot_wait_seconds: float = 0.0,
        ) -> None:
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            live -= 1

        callback.handle_message = slow_handler  # type: ignore[method-assign]

        await asyncio.gather(
            *[callback.run_within_processing_slot(MagicMock()) for _ in range(25)]
        )

        assert peak == 25, "a zero prefetch must not serialise the worker"


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


class TestSlotWaitVisibility:
    """
    The time a delivery spends held in this process waiting for a slot is not queue
    time in the broker, so it has to be reported explicitly or it is invisible.
    """

    async def test_wait_is_measured_and_handed_to_the_handler(self) -> None:
        callback, _ = build_callback()
        # One slot forces the deliveries to queue up behind each other.
        callback.consumer.config.default_prefetch_count = 1

        observed: list[float] = []

        async def slow_handler(
            aio_pika_message: AbstractIncomingMessage,
            slot_wait_seconds: float = 0.0,
        ) -> None:
            observed.append(slot_wait_seconds)
            await asyncio.sleep(0.05)

        callback.handle_message = slow_handler  # type: ignore[method-assign]

        await asyncio.gather(
            *[callback.run_within_processing_slot(MagicMock()) for _ in range(3)]
        )

        # First runs straight away; the others wait for the ones ahead of them.
        assert observed[0] == pytest.approx(0, abs=0.02)
        assert observed[1] == pytest.approx(0.05, abs=0.03)
        assert observed[2] == pytest.approx(0.10, abs=0.04)

    async def test_wait_reaches_the_transaction_data(self) -> None:
        callback, _ = build_callback()
        callback.consumer.config.default_prefetch_count = 1

        transaction_data: list[MessageBusTransactionData] = []

        @asynccontextmanager
        async def capture(
            app_context: AppTransactionContext,
        ) -> AsyncGenerator[None, None]:
            assert isinstance(app_context.transaction_data, MessageBusTransactionData)
            transaction_data.append(app_context.transaction_data)
            yield

        callback.consumer.uow_context_provider = capture  # type: ignore[assignment]

        message = build_message()
        message.headers = {}
        handler_data = cast(Any, callback.message_handler)
        handler_data.instance_callable = AsyncMock()
        handler_data.controller_member.member_function = AsyncMock()

        with patch.object(
            MessageHandler,
            "validate_decorated_fn",
            return_value=("WRAPPED", SampleTaskMessage),
        ), patch.object(
            MessageHandler, "get_last", return_value=MagicMock(timeout=None)
        ):
            await callback.handle_message(message, slot_wait_seconds=1.25)

        assert transaction_data[0].slot_wait_seconds == 1.25

    async def test_metric_is_recorded_for_every_delivery(self) -> None:
        callback, _ = build_callback()
        callback.consumer.config.default_prefetch_count = 2

        samples: list[dict[str, Any]] = []

        with patch(
            "jararaca.messagebus.worker.record_message_slot_wait",
            side_effect=lambda **kwargs: samples.append(kwargs),
        ):
            callback.consumer.report_slot_wait(
                topic="sample.task",
                queue_name="sample-queue",
                channel_id="DEFAULT",
                waited_seconds=0.0,
            )

        # Zero is a meaningful sample: it is how "not saturated" looks on the histogram.
        assert samples == [
            {
                "topic": "sample.task",
                "queue_name": "sample-queue",
                "duration_seconds": 0.0,
            }
        ]

    async def test_saturation_is_logged_only_past_the_threshold(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        callback, _ = build_callback()

        with caplog.at_level(logging.WARNING, logger="jararaca.messagebus.worker"):
            callback.consumer.report_slot_wait(
                topic="sample.task",
                queue_name="sample-queue",
                channel_id="DEFAULT",
                waited_seconds=SLOT_WAIT_WARNING_THRESHOLD_SECONDS / 2,
            )
            assert caplog.records == []

            callback.consumer.report_slot_wait(
                topic="sample.task",
                queue_name="sample-queue",
                channel_id="DEFAULT",
                waited_seconds=SLOT_WAIT_WARNING_THRESHOLD_SECONDS + 1,
            )

        assert len(caplog.records) == 1
        assert "for a free processing slot" in caplog.records[0].getMessage()
        assert "saturated" in caplog.records[0].getMessage()

    async def test_unbounded_channel_reports_no_wait(self) -> None:
        callback, _ = build_callback()
        callback.consumer.config.default_prefetch_count = 0

        async with callback.consumer.processing_slot("DEFAULT") as waited:
            assert waited == 0.0
