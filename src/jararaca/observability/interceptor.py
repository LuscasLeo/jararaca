import logging
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncGenerator, Generator

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.http._log_exporter import (
    OTLPLogExporter as LogExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as SpanExporter,
)
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
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


class ObservabilityInterceptor(AppInterceptor, AppInterceptorWithLifecycle):

    def __init__(self) -> None:
        self._tracing_ctx_provider: TracingContextProviderFactory = (
            OtelTracingContextProviderFactory()
        )

    @asynccontextmanager
    async def intercept(self, app_context: AppContext) -> AsyncGenerator[None, None]:

        async with self._tracing_ctx_provider.root_setup(app_context):

            with provide_tracing_ctx_provider(
                self._tracing_ctx_provider.provide_provider(app_context)
            ):
                yield

    @asynccontextmanager
    async def lifecycle(
        self, app: Microservice, container: Container
    ) -> AsyncGenerator[None, None]:

        resource = Resource(attributes={SERVICE_NAME: "jararaca-example"})
        span_exporter = SpanExporter(endpoint="http://localhost:4318/v1/traces")

        provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(provider)

        span_processor = BatchSpanProcessor(span_exporter)

        provider.add_span_processor(span_processor)

        ####

        logs_exporter = LogExporter(endpoint="http://localhost:4318/v1/logs")
        set_logger_provider(logger_provider := LoggerProvider(resource=resource))
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(logs_exporter))

        logging_handler = LoggingHandler(
            level=logging.DEBUG, logger_provider=logger_provider
        )

        logging.getLogger().addHandler(logging_handler)
        logging.getLogger().addHandler(logging.StreamHandler())
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger().info("Logging initialized")

        yield
