# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Worker readiness file, consumer setup failures, basic.cancel ordering and the
drain timeout, exercised through the real `consume()` with a fake broker."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aio_pika.exceptions import ChannelNotFoundEntity

from jararaca.messagebus.worker import (
    DRAIN_TIMEOUT_ENV_VAR,
    FAIL_ON_CONSUMER_SETUP_ERROR_ENV_VAR,
    READY_FILE_ENV_VAR,
    AioPikaMicroserviceConsumer,
    AioPikaWorkerConfig,
    WorkerStartupError,
    create_message_bus_consumer,
)
from jararaca.utils.rabbitmq_utils import RabbitmqUtils
from jararaca.utils.retry import RetryPolicy


def scheduled_action(name: str) -> MagicMock:
    action = MagicMock()
    action.callable.__module__ = "app.jobs"
    action.callable.__qualname__ = name
    action.spec.channel_id = None
    return action


def queue_name(name: str) -> str:
    return f"app.jobs.{name}"


class FakeBroker:
    """Queues, channels and connection standing in for RabbitMQ, recording events."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.queues: dict[str, MagicMock] = {}
        self.setup_gates: dict[str, asyncio.Event] = {}
        self.missing: set[str] = set()
        self.cancel_delay = 0.0
        self.cancel_hangs = False
        self.connection = self._closable("connection")

    def _closable(self, label: str) -> MagicMock:
        obj = MagicMock()
        obj.is_closed = False

        async def close() -> None:
            self.events.append(f"close:{label}")

        obj.close = AsyncMock(side_effect=close)
        return obj

    def queue(self, name: str) -> MagicMock:
        if name not in self.queues:
            queue = MagicMock()

            async def consume(**kwargs: Any) -> str:
                gate = self.setup_gates.get(name)
                if gate is not None:
                    await gate.wait()
                self.events.append(f"consume:{name}")
                return f"tag-{name}"

            async def cancel(tag: str) -> None:
                if self.cancel_hangs:
                    await asyncio.Event().wait()
                await asyncio.sleep(self.cancel_delay)
                self.events.append(f"cancel:{name}")

            queue.consume = AsyncMock(side_effect=consume)
            queue.cancel = AsyncMock(side_effect=cancel)
            self.queues[name] = queue
        return self.queues[name]

    async def get_queue(self, channel: Any, queue_name: str) -> MagicMock:
        if queue_name in self.missing:
            raise ChannelNotFoundEntity(404, f"NOT_FOUND - no queue '{queue_name}'")
        return self.queue(queue_name)


def build_consumer(
    broker: FakeBroker,
    actions: list[MagicMock],
    **config_overrides: Any,
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
        **config_overrides,
    )
    consumer = AioPikaMicroserviceConsumer(
        broker_backend=MagicMock(),
        config=config,
        message_handler_set=set(),
        scheduled_actions=set(actions),
        uow_context_provider=MagicMock(),
    )

    @asynccontextmanager
    async def connect() -> AsyncGenerator[Any, None]:
        consumer.connection = broker.connection
        yield broker.connection

    async def idle_health_monitor() -> None:
        await consumer.shutdown_event.wait()

    async def retrieve_channel(channel_id: str) -> Any:
        if channel_id not in consumer.channels:
            consumer.channels[channel_id] = broker._closable(f"channel:{channel_id}")
        return consumer.channels[channel_id]

    consumer.connect = connect  # type: ignore[method-assign]
    consumer._verify_infrastructure = AsyncMock(return_value=True)  # type: ignore[method-assign]
    consumer._monitor_connection_health = idle_health_monitor  # type: ignore[method-assign]
    consumer.retrieve_channel = retrieve_channel  # type: ignore[method-assign]
    return consumer


@pytest.fixture
def broker() -> Any:
    fake = FakeBroker()
    with patch.object(RabbitmqUtils, "get_queue", side_effect=fake.get_queue):
        yield fake


async def eventually(condition: Callable[[], bool], timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


def start(consumer: AioPikaMicroserviceConsumer) -> "asyncio.Task[None]":
    return asyncio.create_task(consumer.consume())


async def stop(
    consumer: AioPikaMicroserviceConsumer, task: Awaitable[None], timeout: float = 2.0
) -> None:
    consumer.shutdown()
    await asyncio.wait_for(task, timeout)


class TestReadyFile:
    async def test_written_only_after_every_consumer_is_registered(
        self, broker: FakeBroker, tmp_path: Path
    ) -> None:
        ready = tmp_path / "ready"
        broker.setup_gates[queue_name("slow")] = asyncio.Event()
        consumer = build_consumer(
            broker,
            [scheduled_action("fast"), scheduled_action("slow")],
            ready_file=str(ready),
        )

        task = start(consumer)
        await eventually(lambda: f"consume:{queue_name('fast')}" in broker.events)
        await asyncio.sleep(0.05)
        assert not ready.exists()

        broker.setup_gates[queue_name("slow")].set()
        await eventually(ready.exists)

        await stop(consumer, task)

    async def test_removed_as_soon_as_shutdown_starts(
        self, broker: FakeBroker, tmp_path: Path
    ) -> None:
        ready = tmp_path / "ready"
        consumer = build_consumer(
            broker, [scheduled_action("tick")], ready_file=str(ready)
        )
        task = start(consumer)
        await eventually(ready.exists)

        release = asyncio.Event()
        handler = asyncio.create_task(release.wait())
        consumer.tasks.add(handler)

        consumer.shutdown()
        assert not ready.exists()
        assert not handler.done(), "the drain is still waiting for the handler"

        release.set()
        await asyncio.wait_for(task, 2)

    async def test_stale_file_from_a_previous_run_is_removed_on_boot(
        self, broker: FakeBroker, tmp_path: Path
    ) -> None:
        ready = tmp_path / "ready"
        ready.write_text("old\n")
        broker.setup_gates[queue_name("tick")] = asyncio.Event()
        consumer = build_consumer(
            broker, [scheduled_action("tick")], ready_file=str(ready)
        )

        task = start(consumer)
        await eventually(lambda: not ready.exists())

        broker.setup_gates[queue_name("tick")].set()
        await eventually(ready.exists)
        await stop(consumer, task)

    async def test_no_file_when_not_configured(
        self, broker: FakeBroker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        consumer = build_consumer(broker, [scheduled_action("tick")])
        task = start(consumer)
        await eventually(lambda: f"consume:{queue_name('tick')}" in broker.events)
        await stop(consumer, task)

        assert list(tmp_path.iterdir()) == []


class TestConsumerSetupFailure:
    async def test_ready_file_not_written_when_a_consumer_fails(
        self, broker: FakeBroker, tmp_path: Path
    ) -> None:
        ready = tmp_path / "ready"
        broker.missing.add(queue_name("missing"))
        consumer = build_consumer(
            broker,
            [scheduled_action("ok"), scheduled_action("missing")],
            ready_file=str(ready),
        )

        task = start(consumer)
        await eventually(lambda: f"consume:{queue_name('ok')}" in broker.events)
        await asyncio.sleep(0.05)
        assert not ready.exists()
        assert not task.done(), "without the flag the worker keeps running"

        await stop(consumer, task)

    async def test_fail_flag_stops_the_worker_with_an_error(
        self, broker: FakeBroker, tmp_path: Path
    ) -> None:
        ready = tmp_path / "ready"
        broker.missing.add(queue_name("missing"))
        consumer = build_consumer(
            broker,
            [scheduled_action("ok"), scheduled_action("missing")],
            ready_file=str(ready),
            fail_on_consumer_setup_error=True,
        )

        with pytest.raises(WorkerStartupError):
            await asyncio.wait_for(consumer.consume(), 2)

        assert not ready.exists()
        # The consumer that did start is still stopped cleanly.
        assert f"cancel:{queue_name('ok')}" in broker.events

    async def test_shutdown_during_setup_stops_waiting_for_it(
        self, broker: FakeBroker, tmp_path: Path
    ) -> None:
        ready = tmp_path / "ready"
        broker.setup_gates[queue_name("stuck")] = asyncio.Event()  # never released
        consumer = build_consumer(
            broker, [scheduled_action("stuck")], ready_file=str(ready)
        )

        task = start(consumer)
        await asyncio.sleep(0.05)
        await stop(consumer, task, timeout=1)

        assert not ready.exists()


class TestConsumerCancel:
    async def test_cancel_completes_before_drain_and_close(
        self, broker: FakeBroker
    ) -> None:
        broker.cancel_delay = 0.05
        consumer = build_consumer(
            broker, [scheduled_action("a"), scheduled_action("b")]
        )
        original_drain = consumer.wait_all_tasks_done

        async def recording_drain(timeout: float | None = None) -> None:
            broker.events.append("drain")
            await original_drain(timeout=timeout)

        consumer.wait_all_tasks_done = recording_drain  # type: ignore[method-assign]

        task = start(consumer)
        await eventually(lambda: len(consumer.active_consumers) == 2)
        await stop(consumer, task)

        shutdown_events = [e for e in broker.events if not e.startswith("consume:")]
        cancels, drain, closes = (
            shutdown_events[:2],
            shutdown_events[2],
            shutdown_events[3:],
        )
        assert sorted(cancels) == [
            f"cancel:{queue_name('a')}",
            f"cancel:{queue_name('b')}",
        ]
        assert drain == "drain"
        assert sorted(closes[:2]) == [
            f"close:channel:{queue_name('a')}",
            f"close:channel:{queue_name('b')}",
        ]
        assert closes[2:] == ["close:connection"]

    async def test_cancel_awaited_with_nothing_in_flight(
        self, broker: FakeBroker, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The case that raced before: no handler running, so nothing held the
        shutdown open while basic.cancel was still in flight."""
        broker.cancel_delay = 0.05
        consumer = build_consumer(broker, [scheduled_action("tick")])

        task = start(consumer)
        await eventually(lambda: bool(consumer.active_consumers))
        with caplog.at_level(logging.WARNING):
            await stop(consumer, task)

        queue = broker.queues[queue_name("tick")]
        queue.cancel.assert_awaited_once_with(f"tag-{queue_name('tick')}")
        assert broker.events.index(f"cancel:{queue_name('tick')}") < (
            broker.events.index(f"close:channel:{queue_name('tick')}")
        )
        assert "Channel closed while stopping consumer" not in caplog.text
        assert "Could not cancel the consumer" not in caplog.text

    async def test_unresponsive_cancel_does_not_hold_the_shutdown(
        self, broker: FakeBroker
    ) -> None:
        broker.cancel_hangs = True
        consumer = build_consumer(
            broker, [scheduled_action("tick")], consumer_cancel_timeout=0.05
        )

        task = start(consumer)
        await eventually(lambda: bool(consumer.active_consumers))
        await stop(consumer, task, timeout=1)

        assert "close:connection" in broker.events


class TestDrainTimeout:
    async def test_in_flight_handlers_cancelled_when_it_expires(
        self, broker: FakeBroker
    ) -> None:
        consumer = build_consumer(broker, [scheduled_action("tick")], drain_timeout=0.1)
        task = start(consumer)
        await eventually(lambda: bool(consumer.active_consumers))

        stuck = asyncio.create_task(asyncio.Event().wait())
        consumer.tasks.add(stuck)

        await stop(consumer, task, timeout=2)

        assert stuck.cancelled()
        assert "close:connection" in broker.events

    async def test_without_timeout_waits_for_handlers(self, broker: FakeBroker) -> None:
        consumer = build_consumer(broker, [scheduled_action("tick")])
        task = start(consumer)
        await eventually(lambda: bool(consumer.active_consumers))

        slow = asyncio.create_task(asyncio.sleep(0.2))
        consumer.tasks.add(slow)

        await stop(consumer, task, timeout=2)

        assert slow.done() and not slow.cancelled()


class TestEnvironmentConfiguration:
    URL = "amqp://guest:guest@localhost:5672/?exchange=main&prefetch_count=1"

    def build(self) -> AioPikaWorkerConfig:
        consumer = create_message_bus_consumer(
            broker_url=self.URL,
            broker_backend=MagicMock(),
            scheduled_actions=set(),
            message_handler_set=set(),
            uow_context_provider=MagicMock(),
        )
        assert isinstance(consumer, AioPikaMicroserviceConsumer)
        return consumer.config

    def test_defaults_keep_the_previous_behaviour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in (
            READY_FILE_ENV_VAR,
            FAIL_ON_CONSUMER_SETUP_ERROR_ENV_VAR,
            DRAIN_TIMEOUT_ENV_VAR,
        ):
            monkeypatch.delenv(var, raising=False)

        config = self.build()

        assert config.ready_file is None
        assert config.fail_on_consumer_setup_error is False
        assert config.drain_timeout is None
        assert config.consumer_cancel_timeout == 5.0

    def test_reads_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(READY_FILE_ENV_VAR, "/tmp/jararaca-worker-ready")
        monkeypatch.setenv(FAIL_ON_CONSUMER_SETUP_ERROR_ENV_VAR, "true")
        monkeypatch.setenv(DRAIN_TIMEOUT_ENV_VAR, "45")

        config = self.build()

        assert config.ready_file == "/tmp/jararaca-worker-ready"
        assert config.fail_on_consumer_setup_error is True
        assert config.drain_timeout == 45.0

    @pytest.mark.parametrize("value", ["abc", "0", "-3"])
    def test_invalid_drain_timeout_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv(DRAIN_TIMEOUT_ENV_VAR, value)

        with pytest.raises(ValueError, match=DRAIN_TIMEOUT_ENV_VAR):
            self.build()
