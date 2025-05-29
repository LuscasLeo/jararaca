import asyncio
import inspect
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Generic, Protocol

from fastapi import APIRouter
from fastapi import Depends as DependsF
from fastapi import WebSocketDisconnect
from fastapi.params import Depends
from fastapi.websockets import WebSocket, WebSocketState
from pydantic import BaseModel

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.microservice import (
    AppInterceptor,
    AppInterceptorWithLifecycle,
    AppTransactionContext,
    Microservice,
)
from jararaca.presentation.decorators import (
    RestController,
    UseDependency,
    UseMiddleware,
)
from jararaca.presentation.websocket.context import (
    WebSocketConnectionManager,
    WebSocketMessageSender,
    provide_ws_manager,
    provide_ws_message_sender,
)
from jararaca.presentation.websocket.decorators import (
    INHERITS_WS_MESSAGE,
    WebSocketEndpoint,
)

from .base_types import WebSocketMessageBase


class WebSocketMessageWrapper(BaseModel, Generic[INHERITS_WS_MESSAGE]):

    MESSAGE_ID: str
    message: INHERITS_WS_MESSAGE


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


class WebSocketConnectionManagerImpl(WebSocketConnectionManager):

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

        await self.backend.broadcast(message)

    async def _broadcast_from_backend(self, message: bytes) -> None:
        for websocket in self.all_websockets:
            try:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_bytes(message)
            except WebSocketDisconnect:
                async with self.lock:  # TODO: check if this can cause concurrency slowdown issues
                    self.all_websockets.remove(websocket)

    async def send(self, rooms: list[str], message: WebSocketMessageBase) -> None:

        await self.backend.send(
            rooms,
            WebSocketMessageWrapper(MESSAGE_ID=message.MESSAGE_ID, message=message)
            .model_dump_json()
            .encode(),
        )

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


class StagingWebSocketMessageSender(WebSocketMessageSender):

    def __init__(self, connection: WebSocketConnectionManager) -> None:
        self.connection = connection
        self.staged_messages: list[tuple[list[str], WebSocketMessageBase]] = []

    async def send(self, rooms: list[str], message: WebSocketMessageBase) -> None:
        self.staged_messages.append((rooms, message))

    async def flush(self) -> None:
        for rooms, message in self.staged_messages:
            await self.connection.send(rooms, message)
        self.staged_messages.clear()


class WebSocketInterceptor(AppInterceptor, AppInterceptorWithLifecycle):
    """
    WebSocketInterceptor is responsible for managing WebSocket connections and
    intercepting WebSocket requests within the application. It integrates with
    the application's lifecycle and provides a router for WebSocket endpoints.

    Attributes:
        backend (WebSocketConnectionBackend): The backend for managing WebSocket connections.
        shutdown_event (asyncio.Event): Event to signal shutdown.
        connection_manager (WebSocketConnectionManager): Manages WebSocket connections.

    Methods:
        lifecycle(app: Microservice, container: Container) -> AsyncGenerator[None, None]:
            Manages the lifecycle of the WebSocket interceptor.

        intercept(app_context: AppContext) -> AsyncGenerator[None, None]:
            Intercepts WebSocket requests within the application context.

        get_ws_router(app: Microservice, container: Container, uow_provider: UnitOfWorkContextProvider) -> APIRouter:
            Generates an API router for WebSocket endpoints.

    Note:
        This class is deprecated and may be removed in future versions.
    """

    def __init__(self, backend: WebSocketConnectionBackend) -> None:
        self.backend = backend
        self.shutdown_event = asyncio.Event()
        self.connection_manager = WebSocketConnectionManagerImpl(
            backend, self.shutdown_event
        )

    @asynccontextmanager
    async def lifecycle(
        self, app: Microservice, container: Container
    ) -> AsyncGenerator[None, None]:

        yield
        self.shutdown_event.set()

    @asynccontextmanager
    async def intercept(
        self, app_context: AppTransactionContext
    ) -> AsyncGenerator[None, None]:

        staging_ws_messages_sender = StagingWebSocketMessageSender(
            self.connection_manager
        )
        with provide_ws_manager(self.connection_manager), provide_ws_message_sender(
            staging_ws_messages_sender
        ):
            yield
            await staging_ws_messages_sender.flush()

    # def __wrap_with_uow_context_provider(
    #     self, uow: UnitOfWorkContextProvider, func: Callable[..., Any]
    # ) -> Callable[[WebSocket], Awaitable[Any]]:
    #     ctx_manager = uow

    #     @wraps(func)
    #     async def wrapper(ws: WebSocket) -> Any:
    #         async with ctx_manager(WebSocketAppContext(websocket=ws)):
    #             return await func(ws)

    #     return wrapper

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

                    route_dependencies: list[Depends] = []
                    for middlewares_by_hook in UseMiddleware.get_middlewares(
                        getattr(controller_type, name)
                    ):
                        middleware_instance = container.get_by_type(
                            middlewares_by_hook.middleware
                        )
                        route_dependencies.append(
                            Depends(middleware_instance.intercept)
                        )

                    for dependency in UseDependency.get_dependencies(
                        getattr(controller_type, name)
                    ):
                        route_dependencies.append(DependsF(dependency.dependency))

                    api_router.add_api_websocket_route(
                        path=final_path,
                        endpoint=getattr(controller, name),
                        dependencies=route_dependencies,
                        **(ws_endpoint.options or {}),
                    )

        return api_router
