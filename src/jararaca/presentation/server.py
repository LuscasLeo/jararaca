from typing import Any, AsyncGenerator, Callable

from fastapi import Depends, FastAPI

from jararaca.di import Container
from jararaca.microservice import Microservice
from jararaca.presentation.decorators import RestController


class UowDependency:

    def __init__(self, app: Microservice):
        self.app = app

    async def __call__(self) -> AsyncGenerator[None, None]:

        ctxs = [interceptor.intercept() for interceptor in self.app.interceptors]

        for ctx in ctxs:
            await ctx.__aenter__()

        yield None

        for ctx in reversed(ctxs):
            await ctx.__aexit__(None, None, None)


def create_http_server(
    app: Microservice,
    factory: Callable[[], FastAPI] | None = None,
) -> FastAPI:
    container = Container(app)

    fastapi_app = factory() if factory is not None else FastAPI()

    fastapi_app.router.dependencies.append(Depends(UowDependency(app)))

    for controller_t in app.controllers:
        controller = RestController.get_controller(controller_t)

        if controller is None:
            continue

        instance: Any = container.get_by_type(controller_t)

        router = controller.get_router_factory()(instance)

        fastapi_app.include_router(router)

    return fastapi_app
