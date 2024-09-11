from contextlib import asynccontextmanager
from typing import AsyncContextManager, AsyncGenerator, Protocol

from jararaca.microservice import (
    AppContext,
    AppInterceptor,
    AppInterceptorWithLifecycle,
    Container,
    Microservice,
)
from jararaca.observability.decorators import (
    TracingContextProviderFactory,
    provide_tracing_ctx_provider,
)


class ObservabilityProvider(Protocol):

    tracing_provider: TracingContextProviderFactory

    def setup(
        self, app: Microservice, container: Container
    ) -> AsyncContextManager[None]: ...


class ObservabilityInterceptor(AppInterceptor, AppInterceptorWithLifecycle):

    def __init__(
        self,
        observability_provider: ObservabilityProvider,
    ) -> None:
        self.observability_provider = observability_provider

    @asynccontextmanager
    async def intercept(self, app_context: AppContext) -> AsyncGenerator[None, None]:

        async with self.observability_provider.tracing_provider.root_setup(app_context):

            with provide_tracing_ctx_provider(
                self.observability_provider.tracing_provider.provide_provider(
                    app_context
                )
            ):
                yield

    @asynccontextmanager
    async def lifecycle(
        self, app: Microservice, container: Container
    ) -> AsyncGenerator[None, None]:

        async with self.observability_provider.setup(app, container):
            yield
