import asyncio
import logging
import os
import random
from typing import Annotated, Any, AsyncGenerator

from fastapi import Request
from fastapi.params import Header, Query
from opentelemetry import metrics

from examples.client import HelloRPC
from examples.schemas import HelloResponse
from jararaca import Get, RestController, Token
from jararaca.observability.decorators import TracedFunc
from jararaca.presentation.decorators import Post, UseMiddleware
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
        api_key: Annotated[str, Header(alias="X-API-Key")],
        user_id: Annotated[str, Query()],
    ) -> AsyncGenerator[None, Any]:
        logger.info("AuthMiddleware - API Key: %s, User ID: %s", api_key, user_id)
        yield


class AdminMiddleware(HttpMiddleware):

    async def intercept(
        self,
        admin_token: Annotated[str, Header(alias="X-Admin-Token")],
    ) -> AsyncGenerator[None, Any]:
        logger.info("AdminMiddleware - Admin Token: %s", admin_token)
        yield


class UserAccessMiddleware(HttpMiddleware):
    """
    Middleware that checks user access using user_id from the path parameter.
    This demonstrates how middleware can access path parameters.
    """

    async def intercept(
        self,
        user_id: str,  # This will be detected as a path parameter if {user_id} is in the path
        access_level: Annotated[str, Header(alias="X-Access-Level")],
    ) -> AsyncGenerator[None, Any]:
        logger.info(
            "UserAccessMiddleware - User ID: %s, Access Level: %s",
            user_id,
            access_level,
        )
        yield


@RestController(
    "/my",
    middlewares=[AuthMiddleware],
)
@UseMiddleware(AdminMiddleware)  # Class-level UseMiddleware decorator
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
    @UseMiddleware(
        UserAccessMiddleware
    )  # Method-level UseMiddleware (will also get class-level AdminMiddleware)
    async def create_response(self, hello_response: HelloResponse) -> HelloResponse:
        return hello_response


@RestController(
    "/users/{user_id}",  # This path contains a user_id parameter
    middlewares=[
        UserAccessMiddleware
    ],  # Middleware can access the user_id path parameter
)
class UserController:
    """
    Controller that demonstrates middleware accessing path parameters.
    The UserAccessMiddleware will have access to the user_id path parameter.
    """

    @Get("/profile")
    async def get_user_profile(self, user_id: str) -> dict[str, str]:
        return {"user_id": user_id, "profile": "user profile data"}

    @Post("/settings")
    async def update_user_settings(
        self, user_id: str, settings: dict[str, Any]
    ) -> dict[str, Any]:
        return {"user_id": user_id, "settings": settings}
