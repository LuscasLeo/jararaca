import logging
from contextlib import asynccontextmanager
from typing import AsyncContextManager, AsyncGenerator, Sequence

from jararaca.di import Container
from jararaca.microservice import (
    AppInterceptor,
    Microservice,
    is_interceptor_with_lifecycle,
)

logger = logging.getLogger(__name__)


class AppLifecycle:

    def __init__(self, app: Microservice, container: Container) -> None:
        self.app = app
        self.container = container
        self.__real_interceptors: list[AppInterceptor] | None = None

    @property
    def initialized_interceptors(self) -> Sequence[AppInterceptor]:
        if self.__real_interceptors is None:
            raise ValueError("Interceptors not initialized")
        return self.__real_interceptors

    @asynccontextmanager
    async def __call__(self) -> AsyncGenerator[None, None]:

        self.__real_interceptors = []

        self.container.fill_providers(False)
        lifecycle_ctxs: list[AsyncContextManager[None]] = []

        logger.info("Initializing interceptors lifecycle")
        for interceptor_dep in self.app.interceptors:
            interceptor: AppInterceptor
            if not isinstance(interceptor_dep, AppInterceptor):
                interceptor = self.container.get_or_register_token_or_type(
                    interceptor_dep
                )

            else:
                interceptor = interceptor_dep
            self.__real_interceptors.append(interceptor)

            if not is_interceptor_with_lifecycle(interceptor):
                continue

            ctx = interceptor.lifecycle(self.app, self.container)
            lifecycle_ctxs.append(ctx)

            await ctx.__aenter__()

        self.container.fill_providers(True)

        yield

        logger.info("Finalizing interceptors lifecycle")
        for ctx in lifecycle_ctxs:
            await ctx.__aexit__(None, None, None)
