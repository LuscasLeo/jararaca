from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import Depends, FastAPI

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.presentation.decorators import RestController
from jararaca.presentation.http_microservice import HttpMicroservice


@asynccontextmanager
async def lifespan(api: FastAPI) -> AsyncGenerator[None, None]:
    yield


def create_http_server(
    http_app: HttpMicroservice,
) -> FastAPI:

    app = http_app.app
    factory = http_app.factory
    container = Container(app)

    fastapi_app = factory(lifespan) if factory is not None else FastAPI()

    fastapi_app.router.dependencies.append(
        Depends(UnitOfWorkContextProvider(app, container))
    )

    for controller_t in app.controllers:
        controller = RestController.get_controller(controller_t)

        if controller is None:
            continue

        instance: Any = container.get_by_type(controller_t)

        router = controller.get_router_factory()(instance)

        fastapi_app.include_router(router)

    return fastapi_app
