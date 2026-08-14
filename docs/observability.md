# Observability

Jararaca provides built-in observability support using [OpenTelemetry](https://opentelemetry.io/). It allows you to trace requests across your microservices, collect metrics, and correlate logs with traces.

## Configuration

To enable observability, you need to add the `ObservabilityInterceptor` to your microservice configuration. You can use the `OtelObservabilityProvider` to configure the OpenTelemetry exporters.

```python
import os

from jararaca import Microservice, ObservabilityInterceptor, OtelObservabilityProvider

app = Microservice(
    # ... other configuration ...
    interceptors=[
        ObservabilityInterceptor(
            OtelObservabilityProvider.from_url(
                app_name="my-service",
                url=os.getenv("OTEL_ENDPOINT", "http://localhost:4318"),
            )
        )
    ],
)
```

The `OtelObservabilityProvider.from_url` helper configures the OTLP exporters for traces, metrics, and logs to the specified URL.

### Trace Span Environment Variables

The way HTTP request spans are recorded can be tuned through environment
variables:

| Environment Variable | Type | Default | Description |
|---------------------|------|---------|-------------|
| `JARARACA_OBSERVABILITY_TRACE_SPAN_HTTP_REQUEST_MAX_BODY_SIZE_ATTRIBUTE_VALUE` | `int` | `5000` | Maximum number of bytes of the HTTP request body recorded as a span attribute. Larger bodies are truncated. |
| `JARARACA_OBSERVABILITY_TRACE_SPAN_HTTP_REQUEST_USE_ABSOLUTE_PATH_ON_TITLE` | `bool` | `false` | When enabled, the request span title uses the absolute request path instead of the matched route template. Truthy values: `1`, `true`, `yes`, `on`. |

## Trace Boundaries

With the **child** policy, a message handler execution is recorded as a child of the span
that published the message, so an HTTP request and every task it triggers live inside a
single trace. That keeps the whole arc in one timeline, but it has a cost: the trace
duration includes the time the message spent waiting in the broker, every retry piles onto
the same trace (which may stay open for hours after a dead letter redelivery), and in
Grafana every trace is listed under the name of whatever started it — almost always the
HTTP span.

The default is the **link** policy: the handler execution starts its own trace, so it gets
its own name and its own duration, while the arc stays reconstructable through:

- an OTEL **span link** back to the publish span (plus the `flow.parent_trace_id` and
  `flow.parent_span_id` attributes), and
- a **`flow.id`** attribute shared by every trace of the arc, propagated across processes
  via W3C baggage.

In Grafana, `{ .flow.id = "<id>" }` returns the whole arc as a list of traces, and
`{ .bus.message.topic = "<topic>" && duration > 5s }` finally means what it says.

### The publish span

Handing a message to the broker is recorded as its own `PRODUCER` span named
`PUBLISH <topic>`, and it is *that* span the consumer attaches to — as the link target
under the `link` policy, or as the parent under `child`.

This matters because of the transactional outbox: `message.publish()` only stages the
message, and the actual dispatch happens when the interceptor commits the transaction. The
span that was current at staging time says nothing about when the broker actually received
the message, whereas the publish span pins that moment down with its own start and
duration. It also survives the delay path — a message sent with `delay()` or `schedule()`
carries the publish context in its broker headers, so when the beat worker redispatches it
hours later the consumer still links back to the original publication, not to the beat
dispatch that merely relayed it.

The span carries `bus.message.topic`, `bus.message.name`, `bus.message.module`,
`bus.message.type`, `bus.message.category`, `bus.message.routing_key`,
`messaging.destination.name`, `flow.id`, and `bus.publish.mode` (`immediate` or
`delayed`, with `bus.publish.dispatch_time` for the latter).

### Choosing the policy

| Environment Variable | Type | Default | Description |
|---------------------|------|---------|-------------|
| `JARARACA_OBSERVABILITY_TRACE_ASYNC_BOUNDARY` | `child` \| `link` | `link` | Policy used when a worker picks up a message. |
| `JARARACA_OBSERVABILITY_TRACE_ASYNC_BOUNDARY_ON_RETRY` | `child` \| `link` | `link` | Policy used when the message is being reprocessed (`processing_attempt > 1`), so retries never keep the original trace open. |
| `JARARACA_OBSERVABILITY_TRACE_MESSAGEBUS_SPAN_NAME_STYLE` | `legacy` \| `compact` | `compact` | `legacy` names the root span `Att#1 Message Bus <broker_topic>`; `compact` names it `TASK <message_topic>`, keeping attempt and broker topic as attributes only. |
| `JARARACA_OBSERVABILITY_TRACE_PUBLISH_SPAN` | `bool` | `true` | Record the `PUBLISH <topic>` span and propagate it as the consumer's trace context. When disabled, consumers attach to the producing transaction's root span, as before. |

Individual messages can override the environment defaults, including the retry rule:

```python
from jararaca import Message


class SendCampaignMessage(Message):
    MESSAGE_TOPIC = "campaign.send"
    # This one sits in the queue for a long time and fans out;
    # it gets its own trace instead of inflating the publisher's.
    TRACE_BOUNDARY = "link"


class UpdateReadReceiptMessage(Message):
    MESSAGE_TOPIC = "chat.read-receipt"
    # Fast handoff — keep it in the same timeline as the request that triggered it.
    TRACE_BOUNDARY = "child"
```

Only the message bus is treated as an async boundary. HTTP and WebSocket are synchronous
handoffs and always continue the incoming trace, and the scheduler has no incoming parent
to begin with.

### Reading the flow id

The flow id is available to application code and is attached to every log record emitted
inside the transaction, so the same key correlates traces in Tempo and logs in Loki.

```python
from jararaca import get_flow_id


async def handle(self, message: MessageOf[SendCampaignMessage]) -> None:
    # Store it alongside your own records to jump straight to the whole arc later.
    execution.trace_flow_id = get_flow_id()
```

### Instrumenting a custom publisher

If you publish to a broker jararaca does not manage, wrap the dispatch with
`start_message_publish_span` so consumers get the same handoff point. It yields the
headers to attach to the outgoing message, preserving any application implicit header and
the incoming baggage.

```python
from jararaca import start_message_publish_span

with start_message_publish_span(
    topic=message.MESSAGE_TOPIC,
    destination=exchange_name,
    routing_key=routing_key,
) as headers:
    await exchange.publish(
        aio_pika.Message(body=body, headers={**headers}), routing_key=routing_key
    )
```

## Context Attributes

Jararaca automatically enriches your traces and logs with context-specific attributes. This is handled by the `extract_context_attributes` function, which extracts relevant information based on the current execution context.

### HTTP Context

For HTTP requests, the following attributes are added:

- `http.method`: HTTP method (GET, POST, etc.)
- `http.url`: Full URL
- `http.path`: URL path
- `http.route.path`: Matched route path
- `http.route.endpoint.name`: Name of the endpoint function
- `http.query`: Query string
- `http.request.path_param.*`: All path parameters
- `http.request.query_param.*`: All query parameters
- `http.request.header.*`: All request headers
- `http.request.client.host`: Client IP address
- `http.request.body`: Request body (truncated to 5000 chars)

### Message Bus Context

For message bus handlers:

- `bus.message.id`: Broker message id
- `bus.message.name`: Message class name
- `bus.message.module`: Message class module
- `bus.message.category`: `MESSAGE_CATEGORY` of the message
- `bus.message.type`: `task` or `event`
- `bus.message.topic`: `MESSAGE_TOPIC` of the message
- `bus.message.broker_topic`: Full broker routing key (topic plus handler path)
- `bus.message.processing_attempt`: Delivery attempt, starting at `1`

When the boundary is crossed with the `link` policy, the root span also carries
`flow.id`, `flow.parent_trace_id` and `flow.parent_span_id` (see
[Trace Boundaries](#trace-boundaries)).

### WebSocket Context

For WebSocket connections:

- `ws.url`: WebSocket URL

### Scheduler Context

For scheduled tasks:

- `sched.task_name`: Name of the task
- `sched.scheduled_to`: Scheduled execution time
- `sched.cron_expression`: Cron expression
- `sched.triggered_at`: Actual trigger time

## Tracing Decorators

You can use the `@TracedFunc` decorator to create custom spans for your functions.

```python
from jararaca import TracedFunc


class MyService:
    @TracedFunc("my-operation")
    async def perform_operation(self):
        # This code will run within a child span named "my-operation"
        pass
```

## Logging Integration

Jararaca integrates with the Python `logging` module. When observability is enabled, logs are automatically correlated with the current trace context. The `CustomLoggingHandler` ensures that all the context attributes mentioned above are also attached to your log records.

This means you can filter logs by `http.path`, `bus.message.topic`, `flow.id`, or any other context attribute in your observability backend (e.g., Jaeger, Grafana Tempo, Signoz).

## Class-level Tracing

In addition to `@TracedFunc`, you can use the `@TracedClass` (or `@traced_class`) decorator to automatically trace all async methods within a class. This is useful for services or repositories where you want visibility into all operations without decorating each method individually.

```python
from jararaca import traced_class


@traced_class(trace_name_prefix="UserService")
class UserService:
    async def get_user(self, user_id: str):
        # Traced as "UserService.get_user"
        pass

    async def create_user(self, data: dict):
        # Traced as "UserService.create_user"
        pass

    def helper(self):
        # Not traced (sync method)
        pass
```

You can exclude specific methods or include private methods using the `exclude_methods` and `include_private` arguments.

## Manual Instrumentation

For more granular control, Jararaca provides a set of hooks in `jararaca.observability.hooks` to interact with the current trace context manually.

### Creating Spans

Use `start_span` as a context manager to create a new child span.

```python
from jararaca import start_span


async def complex_logic():
    with start_span("step-1", attributes={"custom.attr": "value"}):
        # Do work
        pass
```

### Adding Events

You can add point-in-time events to the current span.

```python
from jararaca import add_event

add_event("cache-miss", attributes={"key": "user:123"})
```

### Recording Exceptions

To explicitly record an exception in the current span:

```python
from jararaca import record_exception

try:
    ...
except ValueError as e:
    record_exception(e)
    raise
```

### Setting Status and Attributes

You can also update the current span's status or add attributes dynamically.

```python
from jararaca import set_span_status, set_span_attribute

set_span_attribute("user.id", user_id)
set_span_status("ERROR")
```

## Exception Handling

Jararaca automatically configures FastAPI exception handlers to include the trace ID in the response headers. If an error occurs, the response will contain a `traceparent` header (or the configured trace header name), allowing you to easily correlate client-side errors with backend traces.

## Message Bus Metrics

When using the `OtelObservabilityProvider`, Jararaca automatically collects metrics for message bus operations. These metrics help you monitor the health and performance of your message-driven architecture.

### Available Metrics

The following metrics are automatically collected:

- **`messagebus.messages.sent`** *(counter)*: Number of messages published to the message bus
  - Attributes: `topic`, `message_type`, `message_category`

- **`messagebus.messages.processed`** *(counter)*: Number of messages successfully processed
  - Attributes: `topic`, `message_type`, `message_category`, `handler_name`, `handler_method_name`, `broker_topic`

- **`messagebus.messages.failed`** *(counter)*: Number of messages that failed processing
  - Attributes: `topic`, `message_type`, `message_category`, `handler_name`, `handler_method_name`, `broker_topic`

- **`messagebus.messages.inflight`** *(up-down counter)*: Number of messages currently being processed concurrently
  - Incremented by `+1` when processing starts, decremented by `-1` when done
  - Attributes: `topic`, `queue_name`, `message_type`, `message_category`

- **`messagebus.messages.processing_time`** *(histogram)*: Processing duration in seconds per message
  - Attributes: `topic`, `queue_name`, `message_type`, `message_category`, `success`

- **`messagebus.messages.slot_wait_time`** *(histogram)*: Seconds a delivery sat in the worker's memory waiting for a free processing slot on its channel
  - Recorded for every delivery, so zero samples are the "channel not saturated" signal
  - Attributes: `topic`, `queue_name`
  - See [Concurrency bound](messagebus.md#concurrency-bound)

### Example Queries

With these metrics, you can create dashboards and alerts in your observability platform. For example:

- **Success Rate**: `messagebus.messages.processed / (messagebus.messages.processed + messagebus.messages.failed)`
- **Messages Per Topic**: Group by `topic` attribute
- **Task vs Event Distribution**: Group by `message_type` attribute
- **Current Concurrency**: Sum `messagebus.messages.inflight` grouped by `topic`
- **P99 Processing Latency**: Use the `messagebus.messages.processing_time` histogram
- **Channel Saturation**: P99 of `messagebus.messages.slot_wait_time` grouped by `queue_name` — anything consistently above zero means deliveries are queueing inside the worker

### Manual Metric Recording

While metrics are collected automatically, you can also manually record message bus metrics using the provided hooks:

```python
from jararaca.observability.hooks import (
    record_message_inflight,
    record_message_processed,
    record_message_processing_time,
    record_message_sent,
)

# Record a sent message
record_message_sent(
    topic="user.created",
    message_type="event",
    message_category="user_management"
)

# Record a processed message (success or failure)
record_message_processed(
    topic="user.created",
    broker_topic="user.created.myapp.handlers.UserController.handle_user_created",
    handler_name="UserController",
    handler_method_name="handle_user_created",
    message_type="event",
    message_category="user_management",
    success=True,
)

# Track in-flight messages (+1 start, -1 end)
record_message_inflight(
    topic="user.created",
    queue_name="user.created.myapp.handlers.UserController.handle_user_created",
    message_type="event",
    message_category="user_management",
    delta=1,
)

# Record processing duration
record_message_processing_time(
    topic="user.created",
    queue_name="user.created.myapp.handlers.UserController.handle_user_created",
    message_type="event",
    message_category="user_management",
    duration_seconds=0.42,
    success=True,
)
```

These hooks are safe to call even when observability is not configured—they will silently skip metric collection if the observability provider is not available.

## Logger Extra Interceptor

`LoggerExtraInterceptor` is a Python `logging.Handler` that automatically attaches context-scoped key-value attributes to every log record it processes. This complements OpenTelemetry tracing by enriching structured logs with the same contextual attributes without coupling your business code to the logging machinery.

### Setup

Add `LoggerExtraInterceptor` to your root logger (or any specific logger):

```python
import logging

from jararaca import LoggerExtraInterceptor

handler = LoggerExtraInterceptor()
handler.setFormatter(logging.Formatter("%(levelname)s %(message)s [%(request_id)s]"))

logging.getLogger().addHandler(handler)
```

You can also inject additional attributes dynamically at emit time via `inject_extra`:

```python
import logging
from logging import LogRecord

from jararaca import LoggerExtraInterceptor


def add_service_name(record: LogRecord) -> dict:
    return {"service": "order-service"}


handler = LoggerExtraInterceptor(inject_extra=add_service_name)
logging.getLogger().addHandler(handler)
```

### Providing Attributes

Use `providing_logger_extra_attributes` as a context manager anywhere in your request or message handler:

```python
import logging

from jararaca import providing_logger_extra_attributes

logger = logging.getLogger(__name__)


async def process_order(order_id: str) -> None:
    with providing_logger_extra_attributes(order_id=order_id, action="process_order"):
        logger.info("Processing order")  # log record will have order_id and action set
        # ... all nested calls also see these attributes
```

Attributes are *merged* — nested calls extend the current context:

```python
with providing_logger_extra_attributes(request_id="abc"):
    with providing_logger_extra_attributes(user_id="u1"):
        attrs = get_logger_extra_attributes()
        # {"request_id": "abc", "user_id": "u1"}
```

### Reading Attributes Programmatically

```python
from jararaca import get_logger_extra_attributes

attrs = get_logger_extra_attributes()
# Returns the current LoggerExtraAttributeMap (dict[str, str | int | float | bool | None])
```

### Integration with Jararaca Contexts

`LoggerExtraInterceptor` works naturally inside any Jararaca execution context (HTTP, worker, scheduler). Use it alongside `ObservabilityInterceptor` for complete correlated observability: OpenTelemetry spans capture distributed traces while `LoggerExtraInterceptor` enriches your application logs with structured fields.
