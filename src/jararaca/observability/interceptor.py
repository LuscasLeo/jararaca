import logging
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncContextManager, AsyncGenerator, Generator, Protocol

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

from jararaca.microservice import (
    AppContext,
    AppInterceptor,
    AppInterceptorWithLifecycle,
    Container,
    Microservice,
)
from jararaca.observability.decorators import (
    TracingContextProvider,
    TracingContextProviderFactory,
    provide_tracing_ctx_provider,
)

tracer = trace.get_tracer(__name__)


class OtelTracingContextProvider(TracingContextProvider):

    def __init__(self, app_context: AppContext) -> None:
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

    def provide_provider(self, app_context: AppContext) -> TracingContextProvider:
        return OtelTracingContextProvider(app_context)

    @asynccontextmanager
    async def root_setup(self, app_context: AppContext) -> AsyncGenerator[None, None]:

        title: str = "Unmapped App Context Execution"
        headers = {}

        if app_context.context_type == "http":
            print(
                "##############Span is not recording for url %s"
                % app_context.request.url
            )
            headers = dict(app_context.request.headers)
            title = f"HTTP {app_context.request.method} {app_context.request.url}"

        elif app_context.context_type == "message_bus":
            title = f"Message Bus {app_context.topic}"

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


class ObservabilityProvider(Protocol):

    tracing_provider: TracingContextProviderFactory

    def setup(
        self, app: Microservice, container: Container
    ) -> AsyncContextManager[None]: ...


class OtelObservabilityProvider(ObservabilityProvider):

    def __init__(
        self,
        app_name: str,
        logs_exporter: LogExporter,
        span_exporter: SpanExporter,
        meter_exporter: MeterExporter,
    ) -> None:
        self.app_name = app_name
        self.logs_exporter = logs_exporter
        self.span_exporter = span_exporter
        self.meter_exporter = meter_exporter
        self.tracing_provider = OtelTracingContextProviderFactory()

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
        logging_handler.addFilter(
            lambda record: hasattr(record, "message")
            and "localhost" not in record.message
        )
        logging.getLogger().addHandler(logging_handler)
        logging.getLogger().setLevel(logging.DEBUG)

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logging.getLogger().addHandler(stream_handler)

        ### Setup Metrics
        metric_reader = PeriodicExportingMetricReader(
            self.meter_exporter, export_interval_millis=1000
        )
        meter_provider = MeterProvider(metric_readers=[metric_reader])

        metrics.set_meter_provider(meter_provider)

        yield

    @staticmethod
    def from_url(app_name: str, url: str) -> "OtelObservabilityProvider":
        """
        Create an instance of OtelObservabilityProvider with Http Exporters from a given URL
        """

        logs_exporter = LogExporter(endpoint=f"{url}/v1/logs")
        span_exporter = SpanExporter(endpoint=f"{url}/v1/traces")
        metric_exporter = MeterExporter(endpoint=f"{url}/v1/metrics")

        return OtelObservabilityProvider(
            app_name, logs_exporter, span_exporter, metric_exporter
        )


class ObservabilityInterceptor(AppInterceptor, AppInterceptorWithLifecycle):

    def __init__(
        self,
        observability_provider: ObservabilityProvider,
    ) -> None:
        self.observability_provider = observability_provider

    @asynccontextmanager
    async def intercept(self, app_context: AppContext) -> AsyncGenerator[None, None]:

        async with self.observability_provider.tracing_provider.root_setup(app_context):

            with provide_tracing_ctx_provider(
                self.observability_provider.tracing_provider.provide_provider(
                    app_context
                )
            ):
                yield

    @asynccontextmanager
    async def lifecycle(
        self, app: Microservice, container: Container
    ) -> AsyncGenerator[None, None]:

        # resource = Resource(attributes={SERVICE_NAME: "jararaca-example"})
        # span_exporter = SpanExporter(endpoint="http://localhost:4318/v1/traces")

        # provider = TracerProvider(resource=resource)
        # trace.set_tracer_provider(provider)

        # span_processor = BatchSpanProcessor(span_exporter)

        # provider.add_span_processor(span_processor)

        # ####

        # logs_exporter = LogExporter(endpoint="http://localhost:4318/v1/logs")
        # set_logger_provider(logger_provider := LoggerProvider(resource=resource))
        # logger_provider.add_log_record_processor(BatchLogRecordProcessor(logs_exporter))

        # logging_handler = LoggingHandler(
        #     level=logging.DEBUG, logger_provider=logger_provider
        # )

        # logging.getLogger().addHandler(logging_handler)
        # logging.getLogger().addHandler(logging.StreamHandler())
        # logging.getLogger().setLevel(logging.DEBUG)
        # logging.getLogger().info("Logging initialized")
        async with self.observability_provider.setup(app, container):
            yield