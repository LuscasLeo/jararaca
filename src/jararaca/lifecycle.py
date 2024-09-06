from contextlib import asynccontextmanager
from typing import AsyncContextManager, AsyncGenerator

from jararaca.di import Container
from jararaca.microservice import Microservice, is_interceptor_with_lifecycle


class AppLifecycle:

    def __init__(self, app: Microservice, container: Container) -> None:
        self.app = app
        self.container = container

    @asynccontextmanager
    async def __call__(self) -> AsyncGenerator[None, None]:
        lifecycle_ctxs: list[AsyncContextManager[None]] = []

        for interceptor in self.app.interceptors:
            if is_interceptor_with_lifecycle(interceptor):
                ctx = interceptor.lifecycle(self.app)
                lifecycle_ctxs.append(ctx)

        print("Initializing interceptors lifecycle")
        for ctx in lifecycle_ctxs:
            await ctx.__aenter__()

        yield

        print("Finalizing interceptors lifecycle")
        for ctx in lifecycle_ctxs:
            await ctx.__aexit__(None, None, None)
