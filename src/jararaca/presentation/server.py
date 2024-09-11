from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import Depends, FastAPI, Request
from starlette.types import ASGIApp

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.lifecycle import AppLifecycle
from jararaca.microservice import HttpAppContext
from jararaca.presentation.decorators import RestController
from jararaca.presentation.http_microservice import HttpMicroservice
from jararaca.presentation.websocket.websocket_interceptor import WebSocketInterceptor


class HttpAppLifecycle:

    def __init__(
        self, lifecycle: AppLifecycle, uow_provider: UnitOfWorkContextProvider
    ) -> None:
        self.lifecycle = lifecycle
        self.uow_provider = uow_provider

    @asynccontextmanager
    async def __call__(self, api: FastAPI) -> AsyncGenerator[None, None]:
        async with self.lifecycle():

            websocket_interceptors = [
                interceptor
                for interceptor in self.lifecycle.initialized_interceptors
                if isinstance(interceptor, WebSocketInterceptor)
            ]

            for interceptor in websocket_interceptors:
                router = interceptor.get_ws_router(
                    self.lifecycle.app, self.lifecycle.container, self.uow_provider
                )

                api.include_router(router)

            for controller_t in self.lifecycle.app.controllers:
                controller = RestController.get_controller(controller_t)

                if controller is None:
                    continue

                instance: Any = self.lifecycle.container.get_by_type(controller_t)

                router = controller.get_router_factory()(instance)

                api.include_router(router)

            yield


class HttpUowContextProviderDependency:

    def __init__(self, uow_provider: UnitOfWorkContextProvider) -> None:
        self.uow_provider = uow_provider

    async def __call__(self, request: Request) -> AsyncGenerator[None, None]:
        async with self.uow_provider(HttpAppContext(request=request)):
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
        AppLifecycle(app, container),
        uow_provider,
    )

    fastapi_app = (
        factory(lifespan) if factory is not None else FastAPI(lifespan=lifespan)
    )

    fastapi_app.router.dependencies.append(
        Depends(http_uow_context_provider_dependency)
    )

    for middleware in http_app.middlewares:
        middleware_instance = container.get_by_type(middleware)
        fastapi_app.router.dependencies.append(Depends(middleware_instance.intercept))

    return fastapi_app
