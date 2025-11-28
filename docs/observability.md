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

- `bus.topic`: Message topic
- `bus.message.body`: Message payload (JSON)
- `bus.message.name`: Message class name
- `bus.message.module`: Message class module

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

This means you can filter logs by `http.path`, `bus.topic`, or any other context attribute in your observability backend (e.g., Jaeger, Grafana Tempo, Signoz).

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
