import os
import signal
import threading
from contextlib import asynccontextmanager
from signal import SIGINT, SIGTERM
from typing import Any, AsyncGenerator

from fastapi import Depends, FastAPI, Request, WebSocket
from starlette.types import ASGIApp

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.lifecycle import AppLifecycle
from jararaca.microservice import (
    AppTransactionContext,
    HttpTransactionData,
    ShutdownState,
    WebSocketTransactionData,
    provide_shutdown_state,
)
from jararaca.presentation.decorators import RestController
from jararaca.presentation.http_microservice import HttpMicroservice
from jararaca.reflect.controller_inspect import ControllerMemberReflect


class HttpAppLifecycle:

    def __init__(
        self,
        http_app: HttpMicroservice,
        lifecycle: AppLifecycle,
        uow_provider: UnitOfWorkContextProvider,
    ) -> None:
        self.lifecycle = lifecycle
        self.uow_provider = uow_provider
        self.http_app = http_app

    @asynccontextmanager
    async def __call__(self, api: FastAPI) -> AsyncGenerator[None, None]:
        async with self.lifecycle():

            # websocket_interceptors = [
            #     interceptor
            #     for interceptor in self.lifecycle.initialized_interceptors
            #     if isinstance(interceptor, WebSocketInterceptor)
            # ]

            # for interceptor in websocket_interceptors:
            #     router = interceptor.get_ws_router(
            #         self.lifecycle.app, self.lifecycle.container, self.uow_provider
            #     )

            #     api.include_router(router)

            for controller_t in self.lifecycle.app.controllers:
                controller = RestController.get_controller(controller_t)

                if controller is None:
                    continue

                instance: Any = self.lifecycle.container.get_by_type(controller_t)

                # dependencies: list[DependsCls] = []
                # for middleware in controller.middlewares:
                #     middleware_instance = self.lifecycle.container.get_by_type(
                #         middleware
                #     )
                #     dependencies.append(Depends(middleware_instance.intercept))

                router = controller.get_router_factory()(self.lifecycle, instance)

                api.include_router(router)

                for middleware in self.http_app.middlewares:
                    middleware_instance = self.lifecycle.container.get_by_type(
                        middleware
                    )
                    api.router.dependencies.append(
                        Depends(middleware_instance.intercept)
                    )

            yield


class HttpShutdownState(ShutdownState):
    def __init__(self) -> None:
        self._requested = False
        self.old_signal_handlers = {
            SIGINT: signal.getsignal(SIGINT),
            SIGTERM: signal.getsignal(SIGTERM),
        }
        self.thread_lock = threading.Lock()

    def request_shutdown(self) -> None:
        if not self._requested:
            self._requested = True
            os.kill(os.getpid(), SIGINT)

    def is_shutdown_requested(self) -> bool:
        return self._requested

    def handle_signal(self, signum: int, frame: Any) -> None:
        print(f"Received signal {signum}, initiating shutdown...")
        if self._requested:
            print("Shutdown already requested, ignoring signal.")
            return
        print("Requesting shutdown...")
        self._requested = True

        # remove the signal handler to prevent recursion
        for sig in (SIGINT, SIGTERM):
            if self.old_signal_handlers[sig] is not None:
                signal.signal(sig, self.old_signal_handlers[sig])

        signal.raise_signal(signum)

    def setup_signal_handlers(self) -> None:
        signal.signal(SIGINT, self.handle_signal)
        signal.signal(SIGTERM, self.handle_signal)


class HttpUowContextProviderDependency:

    def __init__(self, uow_provider: UnitOfWorkContextProvider) -> None:
        self.uow_provider = uow_provider
        self.shutdown_state = HttpShutdownState()
        self.shutdown_state.setup_signal_handlers()

    async def __call__(
        self, websocket: WebSocket = None, request: Request = None  # type: ignore
    ) -> AsyncGenerator[None, None]:
        if request:
            endpoint = request.scope["endpoint"]
        elif websocket:
            endpoint = websocket.scope["endpoint"]
        else:
            raise ValueError("Either request or websocket must be provided.")

        member = getattr(endpoint, "controller_member_reflect", None)

        if member is None:
            raise ValueError("The endpoint does not have a controller member reflect.")

        assert isinstance(member, ControllerMemberReflect), (
            "Expected endpoint.controller_member_reflect to be of type "
            "ControllerMemberReflect, but got: {}".format(type(member))
        )

        with provide_shutdown_state(self.shutdown_state):
            async with self.uow_provider(
                AppTransactionContext(
                    controller_member_reflect=member,
                    transaction_data=(
                        HttpTransactionData(request=request)
                        if request
                        else WebSocketTransactionData(websocket=websocket)
                    ),
                )
            ):
                yield


def create_http_server(
    http_app: HttpMicroservice,
) -> ASGIApp:

    app = http_app.app
    factory = http_app.factory
    container = Container(app)

    uow_provider = UnitOfWorkContextProvider(app, container)
    http_uow_context_provider_dependency = HttpUowContextProviderDependency(
        uow_provider
    )

    lifespan = HttpAppLifecycle(
        http_app,
        AppLifecycle(app, container),
        uow_provider,
    )

    fastapi_app = (
        factory(lifespan) if factory is not None else FastAPI(lifespan=lifespan)
    )

    fastapi_app.router.dependencies.append(
        Depends(http_uow_context_provider_dependency)
    )

    return fastapi_app
