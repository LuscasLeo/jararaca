# Client Cancellation

When a client gives up on a request (closes the tab, hits a timeout, navigates away), the
server normally keeps working on it until the end: the query runs to completion, the
response is built, and nobody reads it. For expensive reads that is wasted database time,
and under load it is what turns a slow endpoint into a pile up.

`jararaca.presentation.client_cancellation` can cancel such a request instead. Cancelling
the request task cancels whatever it is awaiting; with asyncpg that sends a cancel request
to Postgres, so the abandoned query stops too.

**It is opt-in.** No `Microservice` or `HttpMicroservice` enables it; nothing changes for a
service that does not use the names below.

## When to use it

- Endpoints that run long, read-only queries (listings, searches, reports, exports).
- Services where clients routinely abandon requests (dashboards that refetch on every
  filter change, polling clients with short timeouts).

Prefer restricting it to reads, typically `GET` and `HEAD`. See
[Known side effects](#known-side-effects) for why.

## Setup

Two pieces are required: the **guard** dependency, which does the cancelling, and the
**marker** middleware, which tells the guard when the response has started.

```python
from fastapi import FastAPI
from jararaca import (
    ClientCancellationGuardFor,
    HttpMicroservice,
    create_http_server,
    install_client_cancellation,
)


def fastapi_factory(lifespan):
    app = FastAPI(
        lifespan=lifespan,
        dependencies=[ClientCancellationGuardFor("GET", "HEAD")],
    )
    # ResponseStartedMarker + the 499 exception handler
    install_client_cancellation(app)
    return app


http_app = HttpMicroservice(app, fastapi_factory)
asgi_app = create_http_server(http_app)
```

`install_client_cancellation` does not add the guard: FastAPI copies router dependencies
into each route when the route is declared, so the guard has to be given up front, as above.
To install the pieces by hand:

```python
app.add_middleware(ResponseStartedMarker)
app.add_exception_handler(ClientDisconnected, client_disconnected_exception_handler)
```

### Where to put the guard

Declare it on the `FastAPI(...)` app, as above. Application dependencies are entered before
the unit of work dependency that `create_http_server` appends, so the guard wraps the unit
of work:

- the unit of work sees a `CancelledError`, so nothing is committed (SQLAlchemy's
  `session.begin()` rolls back or invalidates the connection);
- the root span of the request is not marked as failed (OpenTelemetry does not record
  `BaseException`s that are not `Exception`s).

Declaring it on a single endpoint also works (`_: None = ClientCancellationGuardFor("GET")`
as a parameter). The guard then sits inside the unit of work, which sees `ClientDisconnected`
itself: it still rolls back and lets the exception through to its handler (it is never
wrapped in a `PresentationException`), but the root span records it as an error.

## How it works

`ClientCancellationGuardFor(*methods)` returns a `Depends(...)`. For a request whose method
matches (case insensitive; no method means every method, like `ClientCancellationGuard`):

1. A watcher task polls `request.is_disconnected()` every `poll_interval` seconds.
2. If the client disconnects **before the response starts**, the watcher cancels the request
   task.
3. The `CancelledError` unwinds through the endpoint and the dependencies inside the guard.
   When it reaches the guard, the guard calls `task.uncancel()` and raises
   `ClientDisconnected` instead.
4. A `CancelledError` the guard did not cause (server shutdown, an outer timeout) is
   re-raised untouched.
5. After the response has started, the watcher never cancels.

### The marker is mandatory

Once the response has started, uvicorn reports `http.disconnect` as the normal end of the
request cycle, while FastAPI may still be running the teardown of `yield` dependencies,
the unit of work commit among them. `ResponseStartedMarker` records
`http.response.start` in the scope (`scope["app.response_started"]`), and that is the only
way the guard can tell "the client went away" from "the response is done". Without it,
the guard would cancel every request during its own teardown.

So the guard checks that the marker is installed (the marker sets the scope key to `False`
on the way in) and, by default, raises `RuntimeError` on every guarded request when it is
not. Pass `on_missing_marker="warn"` to log a single warning and let requests through
unguarded instead.

### Options

| Parameter | Default | Meaning |
|---|---|---|
| `poll_interval` | `0.5` | Seconds between disconnect checks |
| `scope_key` | `"app.response_started"` | Scope key shared with the marker (`ResponseStartedMarker(app, scope_key=...)`, `install_client_cancellation(app, scope_key=...)`) |
| `exception` | `ClientDisconnected` | Zero argument callable building the exception to raise |
| `on_missing_marker` | `"raise"` | `"raise"` or `"warn"` |

A custom `exception` should subclass `ClientDisconnected`: the unit of work dependency only
lets `ClientDisconnected` (and `HTTPException`) through unwrapped, and
`install_client_cancellation(app, exception_type=...)` must be told the type to register the
handler for.

## Response: 499 Client Closed Request

`client_disconnected_exception_handler` answers

```
HTTP 499
{"detail": "Client Closed Request"}
```

Nobody receives it; it exists so access logs and metrics show the request as abandoned
rather than as a success or a 500. The handler also:

- logs one `WARNING` line, `Client disconnected before the response: GET /path (error=..., traceparent=...)`;
- adds a `client disconnected` event (with an `error` attribute) to the current span,
  **without** marking the span as failed: the service did nothing wrong.

This matches the Go flavour of the framework (jararago, `framework/httpserver/fastapi`).

## Guarding a single query: `QueryGuard`

The guard dependency acts on the whole request. To bound one expensive call, with a timeout
as well, use `QueryGuard` through the `ClientDisconnectionGuard` annotation:

```python
from jararaca import ClientDisconnectionGuard


@Get("/report")
async def report(self, guard: ClientDisconnectionGuard) -> Report:
    rows = await guard.run(self.repository.heavy_query(), timeout=10)
    return Report(rows=rows)
```

`guard.run(awaitable, timeout=None)`:

- returns the awaitable's result;
- raises `ClientDisconnected` and cancels the awaitable if the client disconnects;
- raises `HTTPException(504, "Query timed out")` and cancels the awaitable on timeout;
- cancels the awaitable if the request itself is cancelled.

`QueryGuard(request, poll_interval=0.5, *, exception=ClientDisconnected, timeout_exception=...)`
can be built in your own dependency to change the defaults. `QueryGuard` does not depend on
the marker: it only watches while the awaitable runs, which is always before the response.

## Known side effects

### Ambiguous outcome for writes

Cancelling a request in the middle of a write can leave the client unable to know what
happened. If the disconnect lands while the unit of work is committing, or after a POST
has already called an external service, the work may or may not have taken effect, and
the client never gets the answer. That is why the guard takes a list of methods: keep it
to reads (`GET`, `HEAD`), and only add write methods for endpoints that are idempotent or
whose partial effects are harmless.

### `Invalidate connection ... CancelledError` in the SQLAlchemy logs

When a request is cancelled while a query is running, SQLAlchemy's pool logs at `INFO`:

```
Invalidate connection <AdaptedConnection <asyncpg.connection.Connection ...>> (reason: CancelledError:)
```

This is expected. A connection interrupted mid query is in an unknown state, so the pool
discards it rather than handing it to the next request, and opens a new one when needed.
It is not an error and needs no action; expect it to show up as often as clients abandon
guarded requests.
