from contextlib import asynccontextmanager
from typing import AsyncGenerator

from jararaca.di import Container, provide_container
from jararaca.microservice import AppInterceptor, Microservice


class ContainerInterceptor(AppInterceptor):

    def __init__(self, container: Container) -> None:
        self.container = container

    @asynccontextmanager
    async def intercept(self) -> AsyncGenerator[None, None]:

        with provide_container(self.container):
            yield None


class UnitOfWorkContextProvider:

    def __init__(self, app: Microservice, container: Container):
        self.app = app
        self.container = container
        self.container_interceptor = ContainerInterceptor(container)

    # TODO: Guarantee that the context is closed whenever an exception is raised
    # TODO: Guarantee a unit of work workflow for the whole request, including all the interceptors
    async def __call__(self) -> AsyncGenerator[None, None]:

        ctxs = [self.container_interceptor.intercept()] + [
            interceptor.intercept() for interceptor in self.app.interceptors
        ]

        for ctx in ctxs:
            await ctx.__aenter__()

        yield None

        for ctx in reversed(ctxs):
            await ctx.__aexit__(None, None, None)
