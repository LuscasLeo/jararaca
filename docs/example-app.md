# Example Application

This section provides a complete example of a Jararaca microservice application, demonstrating key features such as:

- **Dependency Injection**: Wiring up services and clients.
- **HTTP RPC Client**: Defining and using a type-safe REST client.
- **Controllers**: Implementing REST endpoints with decorators.
- **Middleware**: Using middleware for authentication and request processing.
- **Observability**: Integrating OpenTelemetry for tracing.
- **Pydantic Models**: Defining data schemas.

## Project Structure

The example application consists of the following files:

```
examples/
├── app.py          # Main application entry point and configuration
├── client.py       # HTTP RPC client definition
├── controller.py   # Controller implementation
└── schemas.py      # Pydantic data models
```

## 1. Schemas (`schemas.py`)

First, we define the data models using Pydantic. These models are used for both request and response bodies.

```python
from pydantic import BaseModel


class HelloResponse(BaseModel):
    message: str
```

## 2. Client (`client.py`)

Next, we define the interface for an external service (or even our own service) using the HTTP RPC client system. This allows us to make type-safe HTTP requests.

```python
from typing import Protocol, runtime_checkable

from examples.schemas import HelloResponse
from jararaca import Body, Get, Post, Query, RestClient


@RestClient("/my")
@runtime_checkable
class HelloRPC(Protocol):

    @Get("/hello")
    @Query("gather")
    async def my_hello(self, gather: bool) -> HelloResponse: ...

    @Get("/ping")
    async def ping(self) -> HelloResponse: ...

    @Post("/create-response")
    @Body("response")
    async def create_response(self, response: HelloResponse) -> HelloResponse: ...
```

## 3. Controller (`controller.py`)

The controller implements the API endpoints. It uses the `HelloRPC` client (injected via DI) to perform some logic. It also demonstrates the use of middleware and observability decorators.

```python
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
from jararaca import (
    Get,
    HttpMiddleware,
    Post,
    RestController,
    Token,
    TracedFunc,
    UseMiddleware,
)

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
```

## 4. Application (`app.py`)

Finally, we wire everything together in the main application file. We configure the `Microservice` with providers (for DI), controllers, and interceptors (for observability).

```python
import logging
import os

from examples.client import HelloRPC
from examples.controller import MyController
from jararaca import (
    HttpMicroservice,
    HttpRpcClientBuilder,
    HTTPXHttpRPCAsyncBackend,
    Microservice,
    ObservabilityInterceptor,
    OtelObservabilityProvider,
    ProviderSpec,
    Token,
    TracedRequestMiddleware,
    create_http_server,
)

logger = logging.getLogger(__name__)


app = Microservice(
    providers=[
        ProviderSpec(
            provide=Token(HelloRPC, "HELLO_RPC"),
            use_value=HttpRpcClientBuilder(
                HTTPXHttpRPCAsyncBackend(prefix_url="http://localhost:8000"),
                middlewares=[TracedRequestMiddleware()],
            ).build(
                HelloRPC  # type: ignore[type-abstract]
            ),
        )
    ],
    controllers=[MyController],
    interceptors=[
        ObservabilityInterceptor(
            OtelObservabilityProvider.from_url(
                "App-example",
                url=os.getenv("OTEL_ENDPOINT", "localhost"),
            )
        )
    ],
)


http_app = create_http_server(HttpMicroservice(app))


loggers = logging.root.manager.loggerDict.get("examples")

if isinstance(loggers, logging.PlaceHolder):

    for logger_name in loggers.loggerMap.keys():
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger_name.addHandler(stream_handler)
        logger_name.setLevel(logging.DEBUG)
```

## Running the Application

To run this example application, you can use the `jararaca server` command. Note that we point to the `Microservice` instance (`app`), not the `FastAPI` app (`http_app`).

```bash
jararaca server examples.app:app --port 8081
```

Output:
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8081 (Press CTRL+C to quit)
```

Or if you want to run it with `uvicorn` directly (pointing to the `FastAPI` app):

```bash
uvicorn examples.app:http_app --reload
```

Once running, you can access the API documentation at `http://localhost:8081/docs`.

## Generating TypeScript Interfaces

You can generate TypeScript interfaces for the client application using the `gen-tsi` command:

```bash
jararaca gen-tsi examples.app:app --stdout
```

This will output the generated TypeScript code, including interfaces for your models and a client class for your controller:

```typescript
/* eslint-disable */
// @ts-nocheck
// noinspection JSUnusedGlobalSymbols

import "@jararaca/core"
import createClassInfiniteQueryHooks
import createClassMutationHooks
import createClassQueryHooks
import HttpBackend
import HttpBackendRequest
import paginationModelByFirstArgPaginationFilter
import recursiveCamelToSnakeCase }
import ResponseType
import { HttpService

// ... helper functions ...

export interface HelloResponse {
  message: string;
}

export class MyController extends HttpService {
    async createResponse(api_key: string, user_id: string, admin_token: string, user_id: string, access_level: string, hello_response: HelloResponse): Promise<HelloResponse> {
        const response = await this.httpBackend.request<HelloResponse>({
            method: "POST",
            path: `/my/create-response`,
            pathParams: {
            },
            headers: {
                "access_level": access_level,
                "admin_token": admin_token,
                "api_key": api_key,
            },
            query: {
                "user_id": user_id,
                "user_id": user_id,
            },
            body: hello_response
        });
        return response;
    }

    async hello(api_key: string, user_id: string, admin_token: string, gather: boolean): Promise<HelloResponse> {
        const response = await this.httpBackend.request<HelloResponse>({
            method: "GET",
            path: `/my/hello`,
            pathParams: {
            },
            headers: {
                "admin_token": admin_token,
                "api_key": api_key,
            },
            query: {
                "gather": gather,
                "user_id": user_id,
            },
            body: undefined
        });
        return response;
    }

    async ping(api_key: string, user_id: string, admin_token: string): Promise<HelloResponse> {
        const response = await this.httpBackend.request<HelloResponse>({
            method: "GET",
            path: `/my/ping`,
            pathParams: {
            },
            headers: {
                "admin_token": admin_token,
                "api_key": api_key,
            },
            query: {
                "user_id": user_id,
            },
            body: undefined
        });
        return response;
    }
}
```
