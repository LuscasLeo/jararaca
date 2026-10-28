# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Opt-in cancellation of HTTP requests whose client has disconnected.

Nothing here is wired by default. A service turns it on by installing
:class:`ResponseStartedMarker` (or calling :func:`install_client_cancellation`) and adding
a guard dependency to the routes it wants guarded::

    app = FastAPI(dependencies=[ClientCancellationGuardFor("GET", "HEAD")])
    install_client_cancellation(app)

Cancelling the request task cancels whatever it is awaiting; for asyncpg that means a
cancel request is sent to Postgres, so an abandoned query stops consuming the database.

**The guard needs the marker.** Once the response has started, uvicorn reports
``http.disconnect`` as the normal end of the request cycle, while FastAPI may still be
tearing down ``yield`` dependencies (the unit of work commit among them). The marker is
how the guard tells those two moments apart; without it every request would be cancelled
during its own teardown. The guard therefore refuses to run when the marker is missing.
"""

import asyncio
import logging
from contextlib import suppress
from typing import (
    Annotated,
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Iterable,
    Literal,
    TypeVar,
)

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jararaca.observability.trace_context import resolve_scope_traceparent

logger = logging.getLogger(__name__)

T = TypeVar("T")

RESPONSE_STARTED_SCOPE_KEY = "app.response_started"
"""Scope key :class:`ResponseStartedMarker` sets, and the guard reads, by default."""

DEFAULT_POLL_INTERVAL = 0.5
"""Seconds between two ``request.is_disconnected()`` checks."""

CLIENT_CLOSED_REQUEST_STATUS = 499
"""Non standard status (nginx) for a request its client gave up on."""

CLIENT_DISCONNECTED_EVENT_NAME = "client disconnected"

MissingMarkerPolicy = Literal["raise", "warn"]

ExceptionFactory = Callable[[], BaseException]


class ClientDisconnected(Exception):
    """The client went away before the response started."""


def _query_timed_out() -> BaseException:
    return HTTPException(status_code=504, detail="Query timed out")


class ResponseStartedMarker:
    """
    ASGI middleware recording in the scope whether the response has started.

    The key is set to ``False`` on the way in, which is also how the guard knows the
    marker is installed, and flipped to ``True`` when ``http.response.start`` goes out.
    """

    def __init__(
        self, app: ASGIApp, *, scope_key: str = RESPONSE_STARTED_SCOPE_KEY
    ) -> None:
        self.app = app
        self.scope_key = scope_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        scope[self.scope_key] = False

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                scope[self.scope_key] = True
            await send(message)

        await self.app(scope, receive, send_wrapper)


def client_cancellation_guard_for(
    methods: Iterable[str] | None = None,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    scope_key: str = RESPONSE_STARTED_SCOPE_KEY,
    exception: ExceptionFactory = ClientDisconnected,
    on_missing_marker: MissingMarkerPolicy = "raise",
) -> Callable[..., AsyncGenerator[None, None]]:
    """
    Build the guard dependency; ``methods=None`` applies it to every HTTP method.

    While the request runs, a watcher polls ``request.is_disconnected()`` every
    *poll_interval* seconds. If the client disconnects before the response starts, the
    request task is cancelled and, once the cancellation unwinds back to the guard, it is
    turned into *exception* (``ClientDisconnected`` by default). A cancellation the guard
    did not cause, such as a server shutdown, propagates untouched. After the response
    has started the watcher never cancels, so dependency teardown always completes.

    *methods* is matched case insensitively. When the marker of *scope_key* is not
    installed the guard raises ``RuntimeError`` (``on_missing_marker="raise"``) or logs a
    warning once and lets the request through unguarded (``"warn"``).
    """
    allowed = None if methods is None else frozenset(m.upper() for m in methods)
    warned_missing_marker = False

    async def guard(request: Request = None) -> AsyncGenerator[None, None]:  # type: ignore[assignment]
        nonlocal warned_missing_marker

        if not request or (
            allowed is not None and request.method.upper() not in allowed
        ):
            yield
            return

        if scope_key not in request.scope:
            message = (
                f"Client cancellation guard requires ResponseStartedMarker"
                f" (scope key {scope_key!r}); without it requests would be cancelled"
                " during dependency teardown. Install it with"
                " app.add_middleware(ResponseStartedMarker) or"
                " install_client_cancellation(app)."
            )
            if on_missing_marker == "raise":
                raise RuntimeError(message)
            if not warned_missing_marker:
                warned_missing_marker = True
                logger.warning(f"{message} Requests are not being guarded.")
            yield
            return

        task = asyncio.current_task()
        disconnected = False

        async def watch() -> None:
            nonlocal disconnected
            while not await request.is_disconnected():
                await asyncio.sleep(poll_interval)
            # after the response starts, uvicorn reports http.disconnect as the normal end of the cycle;
            # cancelling here would interrupt dependency teardown (e.g. the UoW commit)
            if request.scope.get(scope_key):
                return
            disconnected = True
            if task:
                task.cancel()

        watcher = asyncio.create_task(watch())
        try:
            yield
        except asyncio.CancelledError:
            if not disconnected:
                raise  # a real cancellation (e.g. server shutdown); don't swallow it
            if task:
                task.uncancel()
            raise exception()
        finally:
            watcher.cancel()

    return guard


client_cancellation_guard = client_cancellation_guard_for()


def ClientCancellationGuardFor(
    *methods: str,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    scope_key: str = RESPONSE_STARTED_SCOPE_KEY,
    exception: ExceptionFactory = ClientDisconnected,
    on_missing_marker: MissingMarkerPolicy = "raise",
) -> Any:
    """
    Dependency that guards only the given HTTP methods, e.g. ClientCancellationGuardFor("GET").

    With no method it guards every method, like :data:`ClientCancellationGuard`.
    """
    return Depends(
        client_cancellation_guard_for(
            methods or None,
            poll_interval=poll_interval,
            scope_key=scope_key,
            exception=exception,
            on_missing_marker=on_missing_marker,
        )
    )


ClientCancellationGuard = Depends(client_cancellation_guard)


class QueryGuard:
    """Runs awaitables, cancelling them if the client disconnects or a timeout hits."""

    def __init__(
        self,
        request: Request,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        *,
        exception: ExceptionFactory = ClientDisconnected,
        timeout_exception: ExceptionFactory = _query_timed_out,
    ) -> None:
        self.request = request
        self.poll_interval = poll_interval
        self.exception = exception
        self.timeout_exception = timeout_exception

    async def _watch_disconnect(self) -> None:
        while not await self.request.is_disconnected():
            await asyncio.sleep(self.poll_interval)

    async def run(self, aw: Awaitable[T], timeout: float | None = None) -> T:
        task = asyncio.ensure_future(aw)
        watcher = asyncio.create_task(self._watch_disconnect())
        try:
            done, _ = await asyncio.wait(
                {task, watcher}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                return task.result()
            if watcher in done:
                raise self.exception()
            raise self.timeout_exception()
        finally:
            # Runs on success, disconnect, timeout, or if the request itself is cancelled
            watcher.cancel()
            if not task.done():
                task.cancel()  # asyncpg sends a cancel request to Postgres
                with suppress(asyncio.CancelledError):
                    await task


def get_guard(request: Request) -> QueryGuard:
    return QueryGuard(request)


ClientDisconnectionGuard = Annotated[QueryGuard, Depends(get_guard)]


async def client_disconnected_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """
    Answer 499 ``{"detail": "Client Closed Request"}`` for a request its client abandoned.

    Nobody receives the response; it exists for access logs and metrics. The abandonment
    is logged as a single warning and noted as an event on the current span, which is not
    marked as failed: the service did nothing wrong.
    """
    error = repr(exc)
    trace.get_current_span().add_event(
        CLIENT_DISCONNECTED_EVENT_NAME, attributes={"error": error}
    )
    logger.warning(
        "Client disconnected before the response: %s %s (error=%s, traceparent=%s)",
        request.method,
        request.url.path,
        error,
        resolve_scope_traceparent(request.scope),
    )
    return JSONResponse(
        status_code=CLIENT_CLOSED_REQUEST_STATUS,
        content={"detail": "Client Closed Request"},
    )


def install_client_cancellation(
    app: FastAPI,
    *,
    scope_key: str = RESPONSE_STARTED_SCOPE_KEY,
    exception_type: type[Exception] = ClientDisconnected,
    exception_handler: bool = True,
) -> None:
    """
    Install :class:`ResponseStartedMarker` and the 499 exception handler on *app*.

    The guard itself is not added: FastAPI copies router dependencies into each route
    when the route is declared, so it has to be given up front, for instance
    ``FastAPI(dependencies=[ClientCancellationGuardFor("GET", "HEAD")])``.
    """
    app.add_middleware(ResponseStartedMarker, scope_key=scope_key)
    if exception_handler:
        app.add_exception_handler(exception_type, client_disconnected_exception_handler)


__all__ = [
    "ClientDisconnected",
    "ResponseStartedMarker",
    "client_cancellation_guard_for",
    "client_cancellation_guard",
    "ClientCancellationGuardFor",
    "ClientCancellationGuard",
    "QueryGuard",
    "get_guard",
    "ClientDisconnectionGuard",
    "client_disconnected_exception_handler",
    "install_client_cancellation",
    "RESPONSE_STARTED_SCOPE_KEY",
    "DEFAULT_POLL_INTERVAL",
    "CLIENT_CLOSED_REQUEST_STATUS",
]
