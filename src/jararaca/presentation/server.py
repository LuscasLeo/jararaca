from typing import Any, Callable

from fastapi import Depends, FastAPI

from jararaca.core.uow import UnitOfWorkContextProvider
from jararaca.di import Container
from jararaca.microservice import Microservice
from jararaca.presentation.decorators import RestController


def create_http_server(
    app: Microservice,
    factory: Callable[[], FastAPI] | None = None,
) -> FastAPI:
    container = Container(app)

    fastapi_app = factory() if factory is not None else FastAPI()

    fastapi_app.router.dependencies.append(Depends(UnitOfWorkContextProvider(app)))

    for controller_t in app.controllers:
        controller = RestController.get_controller(controller_t)

        if controller is None:
            continue

        instance: Any = container.get_by_type(controller_t)

        router = controller.get_router_factory()(instance)

        fastapi_app.include_router(router)

    return fastapi_app
