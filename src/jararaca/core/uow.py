from contextlib import asynccontextmanager
from typing import AsyncGenerator, Sequence

from jararaca.microservice import (
    AppContext,
    AppInterceptor,
    Container,
    Microservice,
    provide_app_context,
    provide_container,
)


class ContainerInterceptor(AppInterceptor):

    def __init__(self, container: Container) -> None:
        self.container = container

    @asynccontextmanager
    async def intercept(self, app_context: AppContext) -> AsyncGenerator[None, None]:

        with provide_app_context(app_context), provide_container(self.container):
            yield None


class UnitOfWorkContextProvider:

    def __init__(self, app: Microservice, container: Container):
        self.app = app
        self.container = container
        self.container_interceptor = ContainerInterceptor(container)

    # TODO: Guarantee that the context is closed whenever an exception is raised
    # TODO: Guarantee a unit of work workflow for the whole request, including all the interceptors

    def factory_app_interceptors(self) -> Sequence[AppInterceptor]:

        interceptors: list[AppInterceptor] = []

        for interceptor_dep in self.app.interceptors:
            if not isinstance(interceptor_dep, AppInterceptor):
                interceptor = self.container.get_or_register_token_or_type(
                    interceptor_dep
                )
                interceptors.append(interceptor)
            else:
                interceptors.append(interceptor_dep)

        return interceptors

    @asynccontextmanager
    async def __call__(self, app_context: AppContext) -> AsyncGenerator[None, None]:

        app_interceptors = self.factory_app_interceptors()

        ctxs = [self.container_interceptor.intercept(app_context)] + [
            interceptor.intercept(app_context) for interceptor in app_interceptors
        ]

        for ctx in ctxs:
            await ctx.__aenter__()

        yield None

        for ctx in reversed(ctxs):
            await ctx.__aexit__(None, None, None)
