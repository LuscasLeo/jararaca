# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Sequence

from jararaca.microservice import (
    AppInterceptor,
    AppTransactionContext,
    Container,
    Microservice,
    provide_app_context,
    provide_container,
)
from jararaca.reflect.metadata import start_transaction_metadata_context


class ContainerInterceptor(AppInterceptor):

    def __init__(self, container: Container) -> None:
        self.container = container

    @asynccontextmanager
    async def intercept(
        self, app_context: AppTransactionContext
    ) -> AsyncGenerator[None, None]:

        with provide_app_context(app_context), provide_container(self.container):
            yield None


class UnitOfWorkContextProvider:

    def __init__(self, app: Microservice, container: Container):
        self.app = app
        self.container = container
        self.container_interceptor = ContainerInterceptor(container)

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
    async def __call__(
        self, app_context: AppTransactionContext
    ) -> AsyncGenerator[None, None]:

        app_interceptors = self.factory_app_interceptors()
        with start_transaction_metadata_context(
            app_context.controller_member_reflect.metadata
        ):
            ctxs = [self.container_interceptor.intercept(app_context)] + [
                interceptor.intercept(app_context) for interceptor in app_interceptors
            ]

            for ctx in ctxs:
                await ctx.__aenter__()

            exc_type = None
            exc_value = None
            exc_traceback = None

            try:
                yield None
            except BaseException as e:
                exc_type = type(e)
                exc_value = e
                exc_traceback = e.__traceback__
                raise
            finally:
                # Exit interceptors in reverse order, propagating exception info
                for ctx in reversed(ctxs):
                    try:
                        suppressed = await ctx.__aexit__(
                            exc_type, exc_value, exc_traceback
                        )
                        # If an interceptor returns True, it suppresses the exception
                        if suppressed and exc_type is not None:
                            exc_type = None
                            exc_value = None
                            exc_traceback = None
                    except BaseException as exit_exc:
                        # If an interceptor raises an exception during cleanup,
                        # replace the original exception with the new one
                        exc_type = type(exit_exc)
                        exc_value = exit_exc
                        exc_traceback = exit_exc.__traceback__

                # Re-raise the exception if it wasn't suppressed
                if exc_type is not None and exc_value is not None:
                    raise exc_value.with_traceback(exc_traceback)
