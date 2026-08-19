# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    Callable,
    Generator,
    Literal,
    Protocol,
    cast,
)

from opentelemetry import baggage
from opentelemetry import context as context_api
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
from opentelemetry.sdk.resources import SERVICE_NAME, Attributes, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from jararaca.const import (
    OBSERVABILITY_TRACE_ASYNC_BOUNDARY,
    OBSERVABILITY_TRACE_ASYNC_BOUNDARY_ON_RETRY,
    OBSERVABILITY_TRACE_MESSAGEBUS_SPAN_NAME_STYLE,
    OBSERVABILITY_TRACE_PUBLISH_SPAN,
    OBSERVABILITY_TRACE_SPAN_HTTP_REQUEST_MAX_BODY_SIZE_ATTRIBUTE_VALUE,
    OBSERVABILITY_TRACE_SPAN_HTTP_REQUEST_USE_ABSOLUTE_PATH_ON_TITLE,
)
from jararaca.messagebus.implicit_headers import (
    ImplicitHeaders,
    provide_implicit_headers,
    use_implicit_headers,
)
from jararaca.microservice import (
    AppTransactionContext,
    Container,
    MessageBusTransactionData,
    Microservice,
    TransactionData,
    use_app_transaction_context,
)
from jararaca.observability.constants import TRACE_ID_KEY, TRACEPARENT_KEY
from jararaca.observability.decorators import (
    AttributeMap,
    AttributeValue,
    TracingContextProvider,
    TracingContextProviderFactory,
    TracingSpan,
    TracingSpanContext,
)
from jararaca.observability.interceptor import ObservabilityProvider

if TYPE_CHECKING:
    from opentelemetry.trace import Span as _Span
    from typing_extensions import TypeIs

tracer: trace.Tracer = trace.get_tracer(__name__)

FLOW_ID_ATTRIBUTE = "flow.id"
"""
Span attribute holding the id shared by every trace of the same logical flow.

When a boundary is crossed with the "link" policy the arc is split into several traces,
so this attribute is what stitches them back together. Query the whole arc with the
TraceQL expression ``{ .flow.id = "<id>" }``.
"""

FLOW_ID_BAGGAGE_KEY = "flow.id"
"""W3C baggage key used to carry :data:`FLOW_ID_ATTRIBUTE` across process boundaries."""

FLOW_PARENT_TRACE_ID_ATTRIBUTE = "flow.parent_trace_id"

FLOW_PARENT_SPAN_ID_ATTRIBUTE = "flow.parent_span_id"

TraceBoundary = Literal["child", "link"]

_flow_id_ctx = ContextVar[str | None]("flow_id_ctx", default=None)


@contextmanager
def provide_flow_id(flow_id: str) -> Generator[None, Any, None]:
    token = _flow_id_ctx.set(flow_id)
    try:
        yield
    finally:
        with suppress(ValueError):
            _flow_id_ctx.reset(token)


def use_flow_id() -> str | None:
    """Get the flow id of the current transaction, if tracing is active."""
    return _flow_id_ctx.get()


def format_traceparent(span_context: trace.SpanContext) -> str:
    """
    Render *span_context* as a W3C ``traceparent`` header value.

    An invalid span context yields an empty string so that callers can treat "no trace"
    and "no header" as the same case. The propagator does the formatting instead of an
    f-string here so the version prefix stays tied to the spec implementation.
    """
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(
        carrier,
        context=trace.set_span_in_context(trace.NonRecordingSpan(span_context)),
    )
    return carrier.get(TRACEPARENT_KEY, "")


def resolve_trace_boundary(tx_data: TransactionData) -> TraceBoundary:
    """
    Decide whether the root span of this transaction continues the incoming trace
    ("child") or starts a new trace linked back to it ("link").

    Only the message bus is treated as an async boundary. HTTP and WebSocket are
    synchronous handoffs, and the scheduler never has an incoming parent to begin with.
    """
    if not isinstance(tx_data, MessageBusTransactionData):
        return "child"

    policy = getattr(tx_data.message_type, "TRACE_BOUNDARY", "default")

    if policy in ("child", "link"):
        return cast(TraceBoundary, policy)

    if tx_data.processing_attempt > 1:
        return cast(TraceBoundary, OBSERVABILITY_TRACE_ASYNC_BOUNDARY_ON_RETRY)

    return cast(TraceBoundary, OBSERVABILITY_TRACE_ASYNC_BOUNDARY)


def build_message_bus_span_name(tx_data: MessageBusTransactionData) -> str:
    if OBSERVABILITY_TRACE_MESSAGEBUS_SPAN_NAME_STYLE == "compact":
        # Attempt and broker topic stay available as span attributes; keeping them out
        # of the name keeps the Grafana trace list readable and low cardinality.
        return f"{tx_data.message_type.MESSAGE_TYPE.upper()} {tx_data.message_type.MESSAGE_TOPIC}"

    return f"Att#{tx_data.processing_attempt} Message Bus {tx_data.topic}"


PublishMode = Literal["immediate", "delayed"]


@contextmanager
def trace_message_publish(
    *,
    topic: str,
    destination: str | None = None,
    routing_key: str | None = None,
    message_name: str | None = None,
    message_module: str | None = None,
    message_type: str | None = None,
    message_category: str | None = None,
    message_id: str | None = None,
    mode: PublishMode = "immediate",
    dispatch_time: int | None = None,
) -> Generator[ImplicitHeaders, None, None]:
    """
    Record the handoff of a message to the broker as its own PRODUCER span and yield the
    headers that must travel with the message.

    The yielded headers carry the traceparent of *this* span rather than of whatever span
    happened to be active in the producing transaction. That is what makes the consumer
    attach to the exact publish moment: under the transactional outbox pattern the
    ``publish()`` call only stages the message, so the span that was current back then
    says nothing about when the broker actually received it.

    Any non tracing implicit header set by the application is preserved, and so is the
    incoming baggage (and with it the flow id).
    """
    headers: ImplicitHeaders = dict(use_implicit_headers())

    if not OBSERVABILITY_TRACE_PUBLISH_SPAN:
        yield headers
        return

    attributes: dict[str, AttributeValue] = {
        "bus.message.topic": topic,
        "bus.publish.mode": mode,
        "messaging.system": "rabbitmq",
        "messaging.operation": "publish",
    }

    optional_attributes: dict[str, AttributeValue | None] = {
        "bus.message.name": message_name,
        "bus.message.module": message_module,
        "bus.message.type": message_type,
        "bus.message.category": message_category,
        "bus.message.id": message_id,
        "bus.message.routing_key": routing_key,
        "messaging.destination.name": destination,
        "bus.publish.dispatch_time": dispatch_time,
        FLOW_ID_ATTRIBUTE: use_flow_id(),
    }
    attributes.update(
        {key: value for key, value in optional_attributes.items() if value is not None}
    )

    with tracer.start_as_current_span(
        name=f"PUBLISH {topic}",
        kind=trace.SpanKind.PRODUCER,
        attributes=attributes,
    ):
        # Overrides only the trace context keys. `inject` is a no-op when the span is
        # non recording, so a disabled tracer leaves the inherited headers untouched.
        TraceContextTextMapPropagator().inject(headers)

        if "baggage" not in headers:
            # No transaction upstream to inherit baggage from (a publisher running
            # outside a jararaca context), so seed it with the flow id of this span.
            flow_id = use_flow_id() or trace.format_trace_id(
                trace.get_current_span().get_span_context().trace_id
            )
            outgoing_context = baggage.set_baggage(
                FLOW_ID_BAGGAGE_KEY, flow_id, context=context_api.get_current()
            )
            W3CBaggagePropagator().inject(headers, context=outgoing_context)

        yield headers


class MessageBusMetrics:
    """Container for message bus related metrics"""

    def __init__(self, meter: metrics.Meter) -> None:
        self.messages_sent_counter = meter.create_counter(
            name="messagebus.messages.sent",
            description="Number of messages sent to the message bus",
            unit="1",
        )
        self.messages_processed_counter = meter.create_counter(
            name="messagebus.messages.processed",
            description="Number of messages successfully processed from the message bus",
            unit="1",
        )
        self.messages_failed_counter = meter.create_counter(
            name="messagebus.messages.failed",
            description="Number of messages that failed processing",
            unit="1",
        )
        self.messages_processing_time_histogram = meter.create_histogram(
            name="messagebus.messages.processing_time",
            description="Time spent processing messages from the message bus",
            unit="s",
        )
        self.messages_inflight_counter = meter.create_up_down_counter(
            name="messagebus.messages.inflight",
            description="Number of messages currently being processed by the worker",
            unit="1",
        )
        self.messages_slot_wait_time_histogram = meter.create_histogram(
            name="messagebus.messages.slot_wait_time",
            description=(
                "Time a delivery spent held in memory waiting for a free processing "
                "slot on its channel"
            ),
            unit="s",
        )


_message_bus_metrics_ctx = ContextVar[MessageBusMetrics | None](
    "message_bus_metrics_ctx", default=None
)


@contextmanager
def provide_message_bus_metrics(
    metrics_instance: MessageBusMetrics,
) -> Generator[None, Any, None]:
    token = _message_bus_metrics_ctx.set(metrics_instance)
    try:
        yield
    finally:
        with suppress(ValueError):
            _message_bus_metrics_ctx.reset(token)


def use_message_bus_metrics() -> MessageBusMetrics | None:
    """Get the message bus metrics instance if available"""
    return _message_bus_metrics_ctx.get()


def extract_context_attributes(ctx: AppTransactionContext) -> dict[str, Any]:
    tx_data = ctx.transaction_data
    extra_attributes: dict[str, Any] = {}

    if tx_data.context_type == "http":
        extra_attributes = {
            "http.method": tx_data.request.method,
            "http.url": str(tx_data.request.url),
            "http.path": tx_data.request.url.path,
            "http.route.path": tx_data.request.scope["route"].path,
            "http.route.endpoint.name": tx_data.request["route"].endpoint.__qualname__,
            "http.query": tx_data.request.url.query,
            **{
                f"http.request.path_param.{k}": v
                for k, v in tx_data.request.path_params.items()
            },
            **{
                f"http.request.query_param.{k}": v
                for k, v in tx_data.request.query_params.items()
            },
            **{
                f"http.request.header.{k}": v
                for k, v in tx_data.request.headers.items()
            },
            "http.request.client.host": (
                tx_data.request.client.host if tx_data.request.client else ""
            ),
        }
    elif tx_data.context_type == "message_bus":
        extra_attributes = {
            "bus.message.id": tx_data.message_id,
            "bus.message.name": tx_data.message_type.__qualname__,
            "bus.message.module": tx_data.message_type.__module__,
            "bus.message.category": tx_data.message_type.MESSAGE_CATEGORY,
            "bus.message.type": tx_data.message_type.MESSAGE_TYPE,
            "bus.message.topic": tx_data.message_type.MESSAGE_TOPIC,
            "bus.message.broker_topic": tx_data.topic,
            "bus.message.processing_attempt": tx_data.processing_attempt,
            "bus.message.slot_wait_seconds": tx_data.slot_wait_seconds,
        }
    elif tx_data.context_type == "websocket":
        extra_attributes = {
            "ws.url": str(tx_data.websocket.url),
        }
    elif tx_data.context_type == "scheduler":
        extra_attributes = {
            "sched.task_name": tx_data.task_name,
            "sched.scheduled_to": tx_data.scheduled_to.isoformat(),
            "sched.cron_expression": tx_data.cron_expression,
            "sched.triggered_at": tx_data.triggered_at.isoformat(),
            "sched.slot_wait_seconds": tx_data.slot_wait_seconds,
        }
    return {
        "app.context_type": tx_data.context_type,
        "controller_member_reflect.rest_controller.class_name": ctx.controller_member_reflect.controller_reflect.controller_class.__qualname__,
        "controller_member_reflect.rest_controller.module": ctx.controller_member_reflect.controller_reflect.controller_class.__module__,
        "controller_member_reflect.member_function.name": ctx.controller_member_reflect.member_function.__qualname__,
        "controller_member_reflect.member_function.module": ctx.controller_member_reflect.member_function.__module__,
        **extra_attributes,
    }


class OtelTracingSpan(TracingSpan):

    def __init__(self, span: trace.Span) -> None:
        self.span = span


class OtelTracingSpanContext(TracingSpanContext):

    def __init__(self, span_context: trace.SpanContext) -> None:
        self.span_context = span_context


class OtelTracingContextProvider(TracingContextProvider):

    def __init__(self, app_context: AppTransactionContext) -> None:
        self.app_context = app_context

    @contextmanager
    def start_span_context(
        self,
        trace_name: str,
        context_attributes: AttributeMap | None,
    ) -> Generator[None, None, None]:

        with tracer.start_as_current_span(trace_name, attributes=context_attributes):
            yield

    def add_event(
        self, event_name: str, event_attributes: AttributeMap | None = None
    ) -> None:
        trace.get_current_span().add_event(name=event_name, attributes=event_attributes)

    def set_span_status(self, status_code: Literal["OK", "ERROR", "UNSET"]) -> None:
        span = trace.get_current_span()
        if status_code == "OK":
            span.set_status(trace.Status(trace.StatusCode.OK))
        elif status_code == "ERROR":
            span.set_status(trace.Status(trace.StatusCode.ERROR))
        else:
            span.set_status(trace.Status(trace.StatusCode.UNSET))

    def record_exception(
        self,
        exception: Exception,
        attributes: AttributeMap | None = None,
        escaped: bool = False,
    ) -> None:
        span = trace.get_current_span()
        span.record_exception(exception, attributes=attributes, escaped=escaped)

    def set_span_attribute(self, key: str, value: AttributeValue) -> None:
        span = trace.get_current_span()
        span.set_attribute(key, value)

    def update_span_name(self, new_name: str) -> None:
        span = trace.get_current_span()

        span.update_name(new_name)

    def add_link(self, span_context: TracingSpanContext) -> None:
        if not isinstance(span_context, OtelTracingSpanContext):
            return
        span = trace.get_current_span()
        span.add_link(span_context.span_context)

    def get_current_span(self) -> TracingSpan | None:
        return OtelTracingSpan(trace.get_current_span())

    def get_current_span_context(self) -> TracingSpanContext | None:
        return OtelTracingSpanContext(trace.get_current_span().get_span_context())


class OtelTracingContextProviderFactory(TracingContextProviderFactory):

    def provide_provider(
        self, app_context: AppTransactionContext
    ) -> TracingContextProvider:
        return OtelTracingContextProvider(app_context)

    @contextmanager
    def redundant_span_ctx(self, span: trace.Span) -> Generator[trace.Span, Any, None]:
        yield span

    @asynccontextmanager
    async def root_setup(
        self, app_context: AppTransactionContext
    ) -> AsyncGenerator[None, None]:

        title: str = "Unmapped App Context Execution"
        headers: dict[str, Any] = {}
        tx_data = app_context.transaction_data
        extra_attributes = extract_context_attributes(app_context)

        if tx_data.context_type == "http":
            headers = dict(tx_data.request.headers)
            title = f"HTTP {tx_data.request.method} {tx_data.request.scope['route'].path if not OBSERVABILITY_TRACE_SPAN_HTTP_REQUEST_USE_ABSOLUTE_PATH_ON_TITLE else tx_data.request.url.path }"

            if tx_data.request.headers.get("content-type", "").startswith(
                "application/json"
            ):
                extra_attributes["http.request.body"] = (await tx_data.request.body())[
                    :OBSERVABILITY_TRACE_SPAN_HTTP_REQUEST_MAX_BODY_SIZE_ATTRIBUTE_VALUE
                ].decode(errors="ignore")

        elif tx_data.context_type == "message_bus":
            title = build_message_bus_span_name(tx_data)
            headers = use_implicit_headers() or {}

        elif tx_data.context_type == "websocket":
            headers = dict(tx_data.websocket.headers)
            title = f"WebSocket {tx_data.websocket.url}"

        elif tx_data.context_type == "scheduler":
            title = f"Scheduler Task {tx_data.task_name}"

        carrier = {
            key: value
            for key, value in headers.items()
            if key.lower().startswith("traceparent")
            or key.lower().startswith("tracestate")
        }

        current_span_ctx = trace.get_current_span().get_span_context()
        boundary_already_instrumented = (
            current_span_ctx.is_valid and trace.get_current_span().is_recording()
        )

        if boundary_already_instrumented:
            ctx2 = context_api.get_current()
        else:
            ctx = TraceContextTextMapPropagator().extract(carrier)

            b2 = {
                key: value
                for key, value in headers.items()
                if key.lower().startswith("baggage")
            }

            ctx2 = W3CBaggagePropagator().extract(b2, context=ctx)

        incoming_span_context = trace.get_current_span(ctx2).get_span_context()
        boundary = resolve_trace_boundary(tx_data)

        start_context = ctx2

        if boundary == "link" and incoming_span_context.is_valid:

            extra_attributes[FLOW_PARENT_TRACE_ID_ATTRIBUTE] = trace.format_trace_id(
                incoming_span_context.trace_id
            )
            extra_attributes[FLOW_PARENT_SPAN_ID_ATTRIBUTE] = trace.format_span_id(
                incoming_span_context.span_id
            )
            # Drops the remote parent so a fresh trace root is started, while keeping the
            # extracted baggage (and with it the flow id) available for propagation.
            start_context = trace.set_span_in_context(trace.INVALID_SPAN, ctx2)

        if boundary_already_instrumented:
            span = trace.get_current_span()
            start_span_context = self.redundant_span_ctx(span)
            span.set_attributes(extra_attributes)
            span.update_name(title)

        else:
            start_span_context = tracer.start_as_current_span(
                name=title,
                context=start_context,
                links=[
                    trace.Link(
                        incoming_span_context,
                        attributes={"boundary.kind": tx_data.context_type},
                    )
                ],
                attributes={
                    **extra_attributes,
                },
            )

        incoming_flow_id = baggage.get_baggage(FLOW_ID_BAGGAGE_KEY, ctx2)

        with start_span_context as root_span:
            cx = root_span.get_span_context()

            span_trace_id = trace.format_trace_id(cx.trace_id)

            flow_id = (
                str(incoming_flow_id) if incoming_flow_id is not None else span_trace_id
            )
            root_span.set_attribute(FLOW_ID_ATTRIBUTE, flow_id)

            # A full W3C `traceparent`, not the bare trace id: this value is echoed back
            # to the caller as a response header, so it has to be something a compliant
            # propagator can parse.
            response_traceparent = format_traceparent(cx)

            if app_context.transaction_data.context_type == "http":
                app_context.transaction_data.request.scope[TRACEPARENT_KEY] = (
                    response_traceparent
                )
                app_context.transaction_data.request.scope[TRACE_ID_KEY] = span_trace_id
            elif app_context.transaction_data.context_type == "websocket":
                app_context.transaction_data.websocket.scope[TRACEPARENT_KEY] = (
                    response_traceparent
                )
                app_context.transaction_data.websocket.scope[TRACE_ID_KEY] = (
                    span_trace_id
                )

            # The baggage extracted into `ctx2` is not attached to the ambient context by
            # `start_as_current_span`, so the outgoing context is rebuilt explicitly.
            # Otherwise incoming baggage (flow id included) would be dropped at this hop.
            outgoing_context = context_api.get_current()
            for baggage_key, baggage_value in baggage.get_all(ctx2).items():
                outgoing_context = baggage.set_baggage(
                    baggage_key, baggage_value, context=outgoing_context
                )
            outgoing_context = baggage.set_baggage(
                FLOW_ID_BAGGAGE_KEY, flow_id, context=outgoing_context
            )

            tracing_headers: ImplicitHeaders = {}
            TraceContextTextMapPropagator().inject(
                tracing_headers, context=outgoing_context
            )
            W3CBaggagePropagator().inject(tracing_headers, context=outgoing_context)
            with provide_implicit_headers(tracing_headers), provide_flow_id(flow_id):
                yield


class LoggerHandlerCallback(Protocol):

    def __call__(self, logger_handler: logging.Handler) -> None: ...


LoggerHandlerCallbackFunc = Callable[[logging.Handler], None]


class SpanWithName(Protocol):

    @property
    def name(self) -> str: ...


def is_span_with_name(span: Any) -> "TypeIs[SpanWithName]":
    return hasattr(span, "name")


class CustomLoggingHandler(LoggingHandler):

    def _translate(self, record: logging.LogRecord) -> dict[str, Any]:
        try:
            ctx = use_app_transaction_context()
            data = super()._translate(record)
            extra_attributes = extract_context_attributes(ctx)

            current_span: "_Span" = trace.get_current_span()

            flow_id = use_flow_id()

            data["attributes"] = {
                **data.get("attributes", {}),
                **extra_attributes,
                **({FLOW_ID_ATTRIBUTE: flow_id} if flow_id is not None else {}),
                **(
                    {
                        "span_name": (
                            current_span.name if is_span_with_name(current_span) else ""
                        ),
                    }
                    if hasattr(current_span, "name") and current_span.is_recording()
                    else {}
                ),
            }

            return data
        except LookupError:
            return super()._translate(record)


class OtelObservabilityProvider(ObservabilityProvider):

    def __init__(
        self,
        app_name: str,
        *,
        logs_exporter: LogExporter,
        span_exporter: SpanExporter,
        meter_exporter: MeterExporter,
        logging_handler_callback: (
            LoggerHandlerCallback | LoggerHandlerCallbackFunc
        ) = lambda logger_handler: None,
        meter_export_interval: int = 5000,
        attributes: Attributes | None = None,
    ) -> None:
        self.app_name = app_name
        self.logs_exporter = logs_exporter
        self.span_exporter = span_exporter
        self.meter_exporter = meter_exporter
        self.tracing_provider = OtelTracingContextProviderFactory()
        self.meter_export_interval = meter_export_interval
        self.logging_handler_callback = logging_handler_callback
        self.attributes = attributes or {}
        self.__message_bus_metrics: MessageBusMetrics | None = None

    @property
    def message_bus_metrics(self) -> MessageBusMetrics:
        if self.__message_bus_metrics is None:
            raise RuntimeError(
                "MessageBusMetrics is not available outside of the app context"
            )
        return self.__message_bus_metrics

    @asynccontextmanager
    async def setup(
        self, app: Microservice, container: Container
    ) -> AsyncGenerator[None, None]:
        ### Setup Resource

        resource = Resource(
            attributes={**self.attributes} | {SERVICE_NAME: self.app_name}
        )

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

        logging_handler = CustomLoggingHandler(
            level=logging.DEBUG, logger_provider=logger_provider
        )

        # logging_handler.addFilter(lambda _: get_tracing_ctx_provider() is not None)

        self.logging_handler_callback(logging_handler)

        ### Setup Metrics
        metric_reader = PeriodicExportingMetricReader(
            self.meter_exporter, export_interval_millis=self.meter_export_interval
        )
        meter_provider = MeterProvider(
            metric_readers=[metric_reader], resource=resource
        )

        metrics.set_meter_provider(meter_provider)

        # Create message bus metrics
        meter = metrics.get_meter(__name__)
        self.__message_bus_metrics = MessageBusMetrics(meter)

        with provide_message_bus_metrics(self.__message_bus_metrics):
            yield

    @staticmethod
    def from_url(
        app_name: str,
        *,
        url: str,
        logging_handler_callback: (
            LoggerHandlerCallback | LoggerHandlerCallbackFunc
        ) = lambda logger_handler: None,
        meter_export_interval: int = 5000,
        attributes: AttributeMap | None = None,
    ) -> "OtelObservabilityProvider":
        """
        Create an instance of OtelObservabilityProvider with Http Exporters from a given URL
        """

        logs_exporter = LogExporter(endpoint=f"{url}/v1/logs")
        span_exporter = SpanExporter(endpoint=f"{url}/v1/traces")
        metric_exporter = MeterExporter(endpoint=f"{url}/v1/metrics")

        return OtelObservabilityProvider(
            app_name=app_name,
            logs_exporter=logs_exporter,
            span_exporter=span_exporter,
            meter_exporter=metric_exporter,
            logging_handler_callback=logging_handler_callback,
            meter_export_interval=meter_export_interval,
            attributes=attributes,
        )

    @asynccontextmanager
    async def root_setup(
        self, app_context: AppTransactionContext
    ) -> AsyncGenerator[None, None]:
        async with self.tracing_provider.root_setup(app_context):
            with provide_message_bus_metrics(self.message_bus_metrics):
                yield

    def start_provider(
        self, app_context: AppTransactionContext
    ) -> TracingContextProvider:
        return self.tracing_provider.provide_provider(app_context)
