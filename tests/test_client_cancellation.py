# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the opt-in cancellation of requests whose client disconnected.

The app is driven at the ASGI level so the test decides exactly when the client goes
away, relative to the endpoint, the response start and dependency teardown.
"""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Awaitable, Callable

import pytest
from fastapi import Depends, FastAPI, Request
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from starlette.types import Message

from jararaca.presentation.client_cancellation import (
    ClientCancellationGuardFor,
    ClientDisconnected,
    QueryGuard,
    ResponseStartedMarker,
    client_disconnected_exception_handler,
    install_client_cancellation,
)

POLL = 0.01
WAIT = 2.0


class Client:
    """One HTTP request driven by hand: the test triggers the disconnect."""

    def __init__(self, app: Any, method: str = "GET", path: str = "/") -> None:
        self.app = app
        self.scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"test")],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }
        self.disconnected = asyncio.Event()
        self.body_sent = asyncio.Event()
        self.sent: list[Message] = []
        self.request_sent = False
        self.task: asyncio.Task[None] | None = None

    async def receive(self) -> Message:
        if not self.request_sent:
            self.request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self.disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: Message) -> None:
        self.sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body"):
            self.body_sent.set()

    def start(self) -> "Client":
        self.task = asyncio.create_task(self.app(self.scope, self.receive, self.send))
        return self

    def disconnect(self) -> None:
        self.disconnected.set()

    async def finish(self) -> None:
        assert self.task is not None
        await asyncio.wait_for(self.task, WAIT)

    @property
    def status(self) -> int | None:
        for message in self.sent:
            if message["type"] == "http.response.start":
                return int(message["status"])
        return None

    @property
    def json(self) -> Any:
        return json.loads(
            b"".join(
                m.get("body", b"")
                for m in self.sent
                if m["type"] == "http.response.body"
            )
        )


class Probe:
    """Records what happened to a slow endpoint."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = False
        self.completed = False

    async def sleep(self, seconds: float) -> None:
        self.entered.set()
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.completed = True


def build_app(
    guard: Any, *, marker: bool = True, handler: bool = True
) -> tuple[FastAPI, Probe]:
    app = FastAPI(dependencies=[guard])
    probe = Probe()

    @app.get("/slow")
    @app.post("/slow")
    async def slow() -> dict[str, bool]:
        await probe.sleep(0.3)
        return {"ok": True}

    if marker:
        app.add_middleware(ResponseStartedMarker)
    if handler:
        app.add_exception_handler(
            ClientDisconnected, client_disconnected_exception_handler
        )
    return app, probe


async def disconnect_once_entered(client: Client, probe: Probe) -> None:
    client.start()
    await asyncio.wait_for(probe.entered.wait(), WAIT)
    client.disconnect()
    await client.finish()


async def test_disconnect_before_the_response_cancels_and_answers_499() -> None:
    app, probe = build_app(ClientCancellationGuardFor("GET", poll_interval=POLL))
    client = Client(app, "GET", "/slow")

    await disconnect_once_entered(client, probe)

    assert probe.cancelled and not probe.completed
    assert client.status == 499
    assert client.json == {"detail": "Client Closed Request"}


async def test_disconnect_before_the_response_raises_client_disconnected() -> None:
    app, probe = build_app(
        ClientCancellationGuardFor(poll_interval=POLL), handler=False
    )
    seen: list[BaseException] = []

    @app.exception_handler(ClientDisconnected)
    async def capture(request: Request, exc: ClientDisconnected) -> Any:
        seen.append(exc)
        return await client_disconnected_exception_handler(request, exc)

    await disconnect_once_entered(Client(app, "GET", "/slow"), probe)

    assert len(seen) == 1 and isinstance(seen[0], ClientDisconnected)


async def test_custom_exception_is_raised_instead() -> None:
    class Gone(Exception):
        pass

    app, probe = build_app(
        ClientCancellationGuardFor(poll_interval=POLL, exception=Gone), handler=False
    )
    app.add_exception_handler(Gone, client_disconnected_exception_handler)
    client = Client(app, "GET", "/slow")

    await disconnect_once_entered(client, probe)

    assert client.status == 499


async def test_disconnect_after_the_response_started_does_not_cancel_teardown() -> None:
    teardown = Probe()

    async def slow_teardown() -> AsyncGenerator[None, None]:
        yield
        # FastAPI runs this after the response went out; the client is gone by then.
        await teardown.sleep(0.2)

    app = FastAPI(
        dependencies=[
            ClientCancellationGuardFor(poll_interval=POLL),
            Depends(slow_teardown),
        ]
    )

    @app.get("/fast")
    async def fast() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(ResponseStartedMarker)
    client = Client(app, "GET", "/fast").start()

    await asyncio.wait_for(client.body_sent.wait(), WAIT)
    client.disconnect()
    await client.finish()

    assert client.status == 200
    assert teardown.entered.is_set()
    assert teardown.completed and not teardown.cancelled


async def test_a_real_cancellation_propagates() -> None:
    app, probe = build_app(ClientCancellationGuardFor(poll_interval=POLL))
    client = Client(app, "GET", "/slow").start()
    assert client.task is not None

    await asyncio.wait_for(probe.entered.wait(), WAIT)
    client.task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await client.finish()
    assert client.status is None


async def test_methods_outside_the_filter_are_not_guarded() -> None:
    app, probe = build_app(ClientCancellationGuardFor("get", poll_interval=POLL))
    client = Client(app, "POST", "/slow")

    await disconnect_once_entered(client, probe)

    assert probe.completed and not probe.cancelled
    assert client.status == 200


async def test_methods_are_matched_case_insensitively() -> None:
    app, probe = build_app(ClientCancellationGuardFor("get", poll_interval=POLL))
    client = Client(app, "GET", "/slow")

    await disconnect_once_entered(client, probe)

    assert probe.cancelled
    assert client.status == 499


async def test_the_guard_refuses_to_run_without_the_marker() -> None:
    app, probe = build_app(ClientCancellationGuardFor(poll_interval=POLL), marker=False)
    client = Client(app, "GET", "/slow").start()

    with pytest.raises(RuntimeError, match="ResponseStartedMarker"):
        await client.finish()
    assert not probe.entered.is_set()


async def test_the_guard_can_warn_and_step_aside_without_the_marker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, probe = build_app(
        ClientCancellationGuardFor(poll_interval=POLL, on_missing_marker="warn"),
        marker=False,
    )
    client = Client(app, "GET", "/slow")

    with caplog.at_level(logging.WARNING):
        await disconnect_once_entered(client, probe)

    assert probe.completed and not probe.cancelled
    assert client.status == 200
    assert "ResponseStartedMarker" in caplog.text


async def test_install_client_cancellation_adds_marker_and_handler() -> None:
    app, probe = build_app(
        ClientCancellationGuardFor(poll_interval=POLL), marker=False, handler=False
    )
    install_client_cancellation(app)
    client = Client(app, "GET", "/slow")

    await disconnect_once_entered(client, probe)

    assert client.status == 499


def build_query_app(
    work: Callable[[QueryGuard], Awaitable[Any]],
) -> FastAPI:
    def fast_guard(request: Request) -> QueryGuard:
        return QueryGuard(request, poll_interval=POLL)

    app = FastAPI()

    @app.get("/query")
    async def query(guard: QueryGuard = Depends(fast_guard)) -> Any:
        return await work(guard)

    install_client_cancellation(app)
    return app


async def test_query_guard_times_out_with_504_and_cancels_the_query() -> None:
    probe = Probe()
    app = build_query_app(lambda guard: guard.run(probe.sleep(5), timeout=0.05))
    client = Client(app, "GET", "/query").start()

    await client.finish()

    assert client.status == 504
    assert client.json == {"detail": "Query timed out"}
    assert probe.cancelled


async def test_query_guard_cancels_the_query_when_the_client_disconnects() -> None:
    probe = Probe()
    app = build_query_app(lambda guard: guard.run(probe.sleep(5)))
    client = Client(app, "GET", "/query")

    await disconnect_once_entered(client, probe)

    assert client.status == 499
    assert probe.cancelled


async def test_query_guard_returns_the_result() -> None:
    async def answer() -> int:
        return 42

    app = build_query_app(lambda guard: guard.run(answer(), timeout=1))
    client = Client(app, "GET", "/query").start()

    await client.finish()

    assert client.status == 200
    assert client.json == 42


async def test_the_handler_notes_an_event_without_failing_the_span(
    caplog: pytest.LogCaptureFixture,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    request = Request(Client(FastAPI(), "GET", "/slow").scope)

    with caplog.at_level(logging.WARNING):
        with provider.get_tracer(__name__).start_as_current_span("server"):
            response = await client_disconnected_exception_handler(
                request, ClientDisconnected()
            )

    assert response.status_code == 499
    (span,) = exporter.get_finished_spans()
    assert [event.name for event in span.events] == ["client disconnected"]
    assert span.status.status_code == StatusCode.UNSET
    assert "Client disconnected before the response: GET /slow" in caplog.text


async def test_client_disconnected_is_not_wrapped_by_the_unit_of_work() -> None:
    from jararaca import (
        Get,
        HttpMicroservice,
        Microservice,
        RestController,
        create_http_server,
    )

    probe = Probe()

    @RestController("/api")
    class SlowController:
        @Get("/slow")
        async def slow(
            self, _: None = ClientCancellationGuardFor(poll_interval=POLL)
        ) -> dict[str, bool]:
            # Declared on the endpoint, the guard sits inside the unit of work, which
            # would turn an unexpected exception into a PresentationException.
            await probe.sleep(0.3)
            return {"ok": True}

    def factory(lifespan: Any) -> FastAPI:
        app = FastAPI(lifespan=lifespan)
        install_client_cancellation(app)
        return app

    server: Any = create_http_server(
        HttpMicroservice(
            Microservice(providers=[], controllers=[SlowController], interceptors=[]),
            factory=factory,
        )
    )
    client = Client(server, "GET", "/api/slow")

    async with server.router.lifespan_context(server):
        await disconnect_once_entered(client, probe)

    assert probe.cancelled
    assert client.status == 499
