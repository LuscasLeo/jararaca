from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import Depends, FastAPI, Request, WebSocket
from starlette.types import ASGIApp

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.lifecycle import AppLifecycle
from jararaca.microservice import HttpAppContext, WebSocketAppContext
from jararaca.presentation.decorators import RestController
from jararaca.presentation.http_microservice import HttpMicroservice


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


class HttpUowContextProviderDependency:

    def __init__(self, uow_provider: UnitOfWorkContextProvider) -> None:
        self.uow_provider = uow_provider

    async def __call__(
        self, websocket: WebSocket = None, request: Request = None  # type: ignore
    ) -> AsyncGenerator[None, None]:
        async with self.uow_provider(
            HttpAppContext(request=request)
            if request
            else WebSocketAppContext(websocket=websocket)
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
