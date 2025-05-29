import logging
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncGenerator, Generator, Protocol

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.http._log_exporter import (
    OTLPLogExporter as LogExporter,
)
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter as MeterExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as SpanExporter,
)
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from jararaca.microservice import AppTransactionContext, Container, Microservice
from jararaca.observability.decorators import (
    TracingContextProvider,
    TracingContextProviderFactory,
    get_tracing_ctx_provider,
)
from jararaca.observability.interceptor import ObservabilityProvider

tracer: trace.Tracer = trace.get_tracer(__name__)


class OtelTracingContextProvider(TracingContextProvider):

    def __init__(self, app_context: AppTransactionContext) -> None:
        self.app_context = app_context

    @contextmanager
    def __call__(
        self,
        trace_name: str,
        context_attributes: dict[str, str],
    ) -> Generator[None, None, None]:

        with tracer.start_as_current_span(trace_name, attributes=context_attributes):
            yield


class OtelTracingContextProviderFactory(TracingContextProviderFactory):

    def provide_provider(
        self, app_context: AppTransactionContext
    ) -> TracingContextProvider:
        return OtelTracingContextProvider(app_context)

    @asynccontextmanager
    async def root_setup(
        self, app_tx_ctx: AppTransactionContext
    ) -> AsyncGenerator[None, None]:

        title: str = "Unmapped App Context Execution"
        headers = {}
        tx_data = app_tx_ctx.transaction_data
        if tx_data.context_type == "http":

            headers = dict(tx_data.request.headers)
            title = f"HTTP {tx_data.request.method} {tx_data.request.url}"

        elif tx_data.context_type == "message_bus":
            title = f"Message Bus {tx_data.topic}"

        carrier = {
            key: value
            for key, value in headers.items()
            if key.lower().startswith("traceparent")
            or key.lower().startswith("tracestate")
        }

        ctx = TraceContextTextMapPropagator().extract(carrier)

        b2 = {
            key: value
            for key, value in headers.items()
            if key.lower().startswith("baggage")
        }

        ctx2 = W3CBaggagePropagator().extract(b2, context=ctx)

        with tracer.start_as_current_span(name=title, context=ctx2):
            yield


class LoggerHandlerCallback(Protocol):

    def __call__(self, logger_handler: logging.Handler) -> None: ...


class OtelObservabilityProvider(ObservabilityProvider):

    def __init__(
        self,
        app_name: str,
        logs_exporter: LogExporter,
        span_exporter: SpanExporter,
        meter_exporter: MeterExporter,
        logging_handler_callback: LoggerHandlerCallback = lambda _: None,
        meter_export_interval: int = 5000,
    ) -> None:
        self.app_name = app_name
        self.logs_exporter = logs_exporter
        self.span_exporter = span_exporter
        self.meter_exporter = meter_exporter
        self.tracing_provider = OtelTracingContextProviderFactory()
        self.meter_export_interval = meter_export_interval
        self.logging_handler_callback = logging_handler_callback

    @asynccontextmanager
    async def setup(
        self, app: Microservice, container: Container
    ) -> AsyncGenerator[None, None]:
        ### Setup Resource

        resource = Resource(attributes={SERVICE_NAME: self.app_name})

        ### Setup Tracing
        provider = TracerProvider(resource=resource)

        trace.set_tracer_provider(provider)

        span_processor = BatchSpanProcessor(self.span_exporter)
        provider.add_span_processor(span_processor)

        ### Setup Logs
        logger_provider = LoggerProvider(resource=resource)

        set_logger_provider(logger_provider)

        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(self.logs_exporter)
        )

        logging_handler = LoggingHandler(
            level=logging.DEBUG, logger_provider=logger_provider
        )

        logging_handler.addFilter(lambda _: get_tracing_ctx_provider() is not None)

        self.logging_handler_callback(logging_handler)

        ### Setup Metrics
        metric_reader = PeriodicExportingMetricReader(
            self.meter_exporter, export_interval_millis=self.meter_export_interval
        )
        meter_provider = MeterProvider(metric_readers=[metric_reader])

        metrics.set_meter_provider(meter_provider)

        yield

    @staticmethod
    def from_url(
        app_name: str,
        url: str,
        logging_handler_callback: LoggerHandlerCallback = lambda _: None,
        meter_export_interval: int = 5000,
    ) -> "OtelObservabilityProvider":
        """
        Create an instance of OtelObservabilityProvider with Http Exporters from a given URL
        """

        logs_exporter = LogExporter(endpoint=f"{url}/v1/logs")
        span_exporter = SpanExporter(endpoint=f"{url}/v1/traces")
        metric_exporter = MeterExporter(endpoint=f"{url}/v1/metrics")

        return OtelObservabilityProvider(
            app_name,
            logs_exporter,
            span_exporter,
            metric_exporter,
            logging_handler_callback,
            meter_export_interval,
        )
