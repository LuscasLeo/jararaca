# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests that OtelObservabilityProvider flushes and shuts down its OTel providers
on teardown, so telemetry buffered by batch processors is not lost when the
process dies by signal before `atexit` handlers run.
"""

import logging
from typing import Any, cast

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk._logs.export import InMemoryLogExporter
from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    MetricExportResult,
    MetricsData,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from jararaca.observability.providers import otel
from jararaca.observability.providers.otel import OtelObservabilityProvider


class InMemoryMetricExporter(MetricExporter):
    def __init__(self) -> None:
        super().__init__()
        self.exported: list[MetricsData] = []

    def export(
        self,
        metrics_data: MetricsData,
        timeout_millis: float = 10_000,
        **kwargs: Any,
    ) -> MetricExportResult:
        self.exported.append(metrics_data)
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        pass

    def metric_names(self) -> set[str]:
        return {
            metric.name
            for data in self.exported
            for resource_metrics in data.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
        }


@pytest.fixture
def captured_providers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture the providers instead of installing them globally (OTel only
    allows setting the global providers once per process)."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        trace, "set_tracer_provider", lambda p: captured.update(tracer=p)
    )
    monkeypatch.setattr(otel, "set_logger_provider", lambda p: captured.update(logs=p))
    monkeypatch.setattr(
        metrics, "set_meter_provider", lambda p: captured.update(meter=p)
    )
    return captured


def build_provider(
    handlers: list[logging.Handler],
) -> tuple[
    OtelObservabilityProvider,
    InMemorySpanExporter,
    InMemoryLogExporter,
    InMemoryMetricExporter,
]:
    span_exporter = InMemorySpanExporter()
    log_exporter = InMemoryLogExporter()  # type: ignore[no-untyped-call]
    metric_exporter = InMemoryMetricExporter()
    provider = OtelObservabilityProvider(
        "teardown-test",
        span_exporter=cast(Any, span_exporter),
        logs_exporter=cast(Any, log_exporter),
        meter_exporter=cast(Any, metric_exporter),
        logging_handler_callback=handlers.append,
        # Long enough that nothing is exported periodically during the test.
        meter_export_interval=600_000,
    )
    return provider, span_exporter, log_exporter, metric_exporter


@pytest.mark.asyncio
async def test_teardown_flushes_pending_telemetry(
    captured_providers: dict[str, Any],
) -> None:
    handlers: list[logging.Handler] = []
    provider, span_exporter, log_exporter, metric_exporter = build_provider(handlers)

    async with provider.setup(cast(Any, None), cast(Any, None)):
        tracer = captured_providers["tracer"].get_tracer("teardown-test")
        with tracer.start_as_current_span("last-span"):
            pass

        test_logger = logging.getLogger("teardown-test")
        test_logger.addHandler(handlers[0])
        test_logger.setLevel(logging.INFO)
        try:
            test_logger.info("last log")
        finally:
            test_logger.removeHandler(handlers[0])

        meter = captured_providers["meter"].get_meter("teardown-test")
        meter.create_counter("last_counter").add(1)

        # Still buffered in the batch processors / periodic reader.
        assert span_exporter.get_finished_spans() == ()
        assert log_exporter.get_finished_logs() == ()
        assert metric_exporter.exported == []

    assert [span.name for span in span_exporter.get_finished_spans()] == ["last-span"]
    assert [log.log_record.body for log in log_exporter.get_finished_logs()] == [
        "last log"
    ]
    assert "last_counter" in metric_exporter.metric_names()


@pytest.mark.asyncio
async def test_teardown_flushes_on_exception_and_preserves_it(
    captured_providers: dict[str, Any],
) -> None:
    provider, span_exporter, _, _ = build_provider([])

    with pytest.raises(RuntimeError, match="boom"):
        async with provider.setup(cast(Any, None), cast(Any, None)):
            tracer = captured_providers["tracer"].get_tracer("teardown-test")
            with tracer.start_as_current_span("span-before-crash"):
                pass
            raise RuntimeError("boom")

    assert [span.name for span in span_exporter.get_finished_spans()] == [
        "span-before-crash"
    ]


@pytest.mark.asyncio
async def test_teardown_failure_in_one_provider_does_not_skip_others(
    captured_providers: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider, _, _, metric_exporter = build_provider([])

    async with provider.setup(cast(Any, None), cast(Any, None)):
        tracer_provider = captured_providers["tracer"]

        def failing_flush(timeout_millis: int = 30_000) -> bool:
            raise RuntimeError("flush failed")

        tracer_provider.force_flush = failing_flush
        meter = captured_providers["meter"].get_meter("teardown-test")
        meter.create_counter("still_exported").add(1)

    assert "still_exported" in metric_exporter.metric_names()
    assert any(
        "flush failed" in str(record.exc_info[1])
        for record in caplog.records
        if record.exc_info
    )
