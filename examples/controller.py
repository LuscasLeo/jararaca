import asyncio
import logging
import os
import random
from typing import Annotated, Any, AsyncGenerator

from fastapi import Request
from opentelemetry import metrics

from examples.client import HelloRPC
from examples.schemas import HelloResponse
from jararaca import Get, RestController, Token
from jararaca.observability.decorators import TracedFunc
from jararaca.presentation.decorators import Post
from jararaca.presentation.http_microservice import HttpMiddleware

logger = logging.getLogger(__name__)


meter = metrics.get_meter(__name__)
hello_counter = meter.create_counter(
    "hello_counter",
    unit="1",
    description="Counts number of hello for each pid",
)


class HelloService:
    def __init__(
        self,
        hello_rpc: Annotated[HelloRPC, Token(HelloRPC, "HELLO_RPC")],
    ):
        self.hello_rpc = hello_rpc

    @TracedFunc("ping")
    async def ping(self) -> HelloResponse:
        return await self.hello_rpc.ping()

    @TracedFunc("hello-service")
    async def hello(
        self,
        gather: bool,
    ) -> HelloResponse:

        now = asyncio.get_event_loop().time()
        if gather:
            await asyncio.gather(*[self.random_await(a) for a in range(10)])
        else:
            for a in range(10):
                await self.random_await(a)
        return HelloResponse(
            message="Elapsed time: {}".format(asyncio.get_event_loop().time() - now)
        )

    @TracedFunc("random-await")
    async def random_await(self, index: int) -> None:
        logger.info("Random await %s", index, extra={"index": index})
        await asyncio.sleep(random.randint(1, 3))
        logger.info("Random await %s done", index, extra={"index": index})


class AuthMiddleware(HttpMiddleware):

    async def intercept(
        self,
    ) -> AsyncGenerator[None, Any]:
        logger.info("AuthMiddleware")
        yield


@RestController(
    "/my",
    middlewares=[AuthMiddleware],
)
class MyController:

    def __init__(
        self,
        hello_service: HelloService,
    ) -> None:
        self.hello_service = hello_service

    @TracedFunc("hello")
    @Get("/hello")
    async def hello(self, gather: bool, request: Request) -> HelloResponse:
        hello_counter.add(1, {"pid": str(os.getpid())})

        logger.info("Hello %s", request.query_params.get("name") or "World")
        res = await self.hello_service.ping()
        return res

    @TracedFunc("ping")
    @Get("/ping")
    async def ping(self) -> HelloResponse:
        return HelloResponse(message="pong")

    @TracedFunc("create-response")
    @Post("/create-response")
    async def create_response(self, hello_response: HelloResponse) -> HelloResponse:
        return hello_response
