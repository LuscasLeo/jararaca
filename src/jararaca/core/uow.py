from typing import AsyncGenerator

from jararaca.microservice import Microservice


class UnitOfWorkContextProvider:

    def __init__(self, app: Microservice):
        self.app = app

    # TODO: Guarantee that the context is closed whenever an exception is raised
    # TODO: Guarantee a unit of work workflow for the whole request, including all the interceptors
    async def __call__(self) -> AsyncGenerator[None, None]:

        ctxs = [interceptor.intercept() for interceptor in self.app.interceptors]

        for ctx in ctxs:
            await ctx.__aenter__()

        yield None

        for ctx in reversed(ctxs):
            await ctx.__aexit__(None, None, None)
