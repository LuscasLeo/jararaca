import asyncio
import inspect
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, AsyncGenerator, Awaitable, Callable, Generator, Protocol

from fastapi import APIRouter, WebSocketDisconnect
from fastapi.websockets import WebSocket, WebSocketState

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.microservice import (
    AppContext,
    AppInterceptor,
    AppInterceptorWithLifecycle,
    Microservice,
    WebSocketAppContext,
)
from jararaca.presentation.decorators import RestController
from jararaca.presentation.websocket.decorators import WebSocketEndpoint


class BroadcastFunc(Protocol):
    async def __call__(self, message: bytes) -> None: ...


class SendFunc(Protocol):
    async def __call__(self, rooms: list[str], message: bytes) -> None: ...


class WebSocketConnectionBackend(Protocol):

    async def broadcast(self, message: bytes) -> None: ...

    async def send(self, rooms: list[str], message: bytes) -> None: ...

    def configure(
        self, broadcast: BroadcastFunc, send: SendFunc, shutdown_event: asyncio.Event
    ) -> None: ...

    async def shutdown(self) -> None: ...


class WebSocketConnectionManager:

    def __init__(
        self, backend: WebSocketConnectionBackend, shutdown_event: asyncio.Event
    ) -> None:
        self.rooms: dict[str, set[WebSocket]] = {}
        self.all_websockets: set[WebSocket] = set()
        self.backend = backend
        self.lock = asyncio.Lock()

        self.backend.configure(
            broadcast=self._broadcast_from_backend,
            send=self._send_from_backend,
            shutdown_event=shutdown_event,
        )

    async def broadcast(self, message: bytes) -> None:

        # for websocket in self.all_websockets:
        #     await websocket.send_bytes(message)

        await self.backend.broadcast(message)

    async def _broadcast_from_backend(self, message: bytes) -> None:
        for websocket in self.all_websockets:
            try:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_bytes(message)
            except WebSocketDisconnect:
                async with self.lock:  # TODO: check if this can cause concurrency slowdown issues
                    self.all_websockets.remove(websocket)

    async def send(self, rooms: list[str], message: bytes) -> None:
        # for room in rooms:
        #     for websocket in self.rooms.get(room, set()):
        #         await websocket.send_bytes(message)

        await self.backend.send(rooms, message)

    async def _send_from_backend(self, rooms: list[str], message: bytes) -> None:
        async with self.lock:
            for room in rooms:
                for websocket in self.rooms.get(room, set()):
                    try:
                        if websocket.client_state == WebSocketState.CONNECTED:
                            await websocket.send_bytes(message)
                    except WebSocketDisconnect:
                        async with self.lock:
                            if websocket in self.rooms[room]:
                                self.rooms[room].remove(websocket)

    async def join(self, rooms: list[str], websocket: WebSocket) -> None:
        for room in rooms:
            self.rooms.setdefault(room, set()).add(websocket)

    async def add_websocket(self, websocket: WebSocket) -> None:
        self.all_websockets.add(websocket)

    async def remove_websocket(self, websocket: WebSocket) -> None:
        self.all_websockets.remove(websocket)
        for room in self.rooms.values():
            room.discard(websocket)

    # async def setup_consumer(self, websocket: WebSocket) -> None: ...


_ws_manage_ctx = ContextVar[WebSocketConnectionManager]("ws_manage_ctx")


def use_ws_manager() -> WebSocketConnectionManager:
    try:
        return _ws_manage_ctx.get()
    except LookupError:
        raise RuntimeError("No WebSocketConnectionManager found")


@contextmanager
def provide_ws_manager(
    ws_manager: WebSocketConnectionManager,
) -> Generator[None, None, None]:
    token = _ws_manage_ctx.set(ws_manager)
    try:
        yield
    finally:
        try:
            _ws_manage_ctx.reset(token)
        except ValueError:
            pass


class WebSocketInterceptor(AppInterceptor, AppInterceptorWithLifecycle):

    def __init__(self, backend: WebSocketConnectionBackend) -> None:
        self.backend = backend
        self.shutdown_event = asyncio.Event()
        self.connection_manager = WebSocketConnectionManager(
            backend, self.shutdown_event
        )

    @asynccontextmanager
    async def lifecycle(
        self, app: Microservice, container: Container
    ) -> AsyncGenerator[None, None]:

        yield
        self.shutdown_event.set()

    @asynccontextmanager
    async def intercept(self, app_context: AppContext) -> AsyncGenerator[None, None]:

        with provide_ws_manager(self.connection_manager):
            yield

    def __wrap_with_uow_context_provider(
        self, uow: UnitOfWorkContextProvider, func: Callable[..., Any]
    ) -> Callable[[WebSocket], Awaitable[Any]]:
        ctx_manager = uow

        @wraps(func)
        async def wrapper(ws: WebSocket) -> Any:
            async with ctx_manager(WebSocketAppContext(websocket=ws)):
                return await func(ws)

        return wrapper

    def get_ws_router(
        self,
        app: Microservice,
        container: Container,
        uow_provider: UnitOfWorkContextProvider,
    ) -> APIRouter:
        api_router = APIRouter(
            tags=["WebSocket"],
        )

        for controller_type in app.controllers:

            rest_controller = RestController.get_controller(controller_type)
            controller: Any = container.get_by_type(controller_type)

            members = inspect.getmembers(controller_type, predicate=inspect.isfunction)

            for name, member in members:
                if (ws_endpoint := WebSocketEndpoint.get(member)) is not None:
                    final_path = (
                        rest_controller.path + ws_endpoint.path
                        if rest_controller
                        else ws_endpoint.path
                    )
                    api_router.add_websocket_route(
                        path=final_path,
                        endpoint=self.__wrap_with_uow_context_provider(
                            func=getattr(controller, name),
                            uow=uow_provider,
                        ),
                        **(ws_endpoint.options or {}),
                    )

        return api_router
