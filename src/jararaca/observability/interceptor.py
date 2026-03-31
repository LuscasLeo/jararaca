# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import asynccontextmanager
from typing import AsyncContextManager, AsyncGenerator, Protocol

from jararaca.microservice import (
    AppInterceptor,
    AppInterceptorWithLifecycle,
    AppTransactionContext,
    Container,
    Microservice,
)
from jararaca.observability.decorators import (
    TracingContextProvider,
    provide_tracing_ctx_provider,
)


class ObservabilityProvider(Protocol):

    def root_setup(
        self, app_context: AppTransactionContext
    ) -> AsyncContextManager[None]: ...

    def start_provider(
        self, app_context: AppTransactionContext
    ) -> TracingContextProvider: ...

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
    async def intercept(
        self, app_context: AppTransactionContext
    ) -> AsyncGenerator[None, None]:

        async with self.observability_provider.root_setup(app_context):

            with provide_tracing_ctx_provider(
                self.observability_provider.start_provider(app_context)
            ):

                yield

    @asynccontextmanager
    async def lifecycle(
        self, app: Microservice, container: Container
    ) -> AsyncGenerator[None, None]:

        async with self.observability_provider.setup(app, container):
            yield
