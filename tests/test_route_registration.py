# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Controller routes are registered on the FastAPI app once, with the same result
``app.include_router(router)`` used to give, and app-wide middlewares reach every
route exactly once.
"""

from typing import Any, AsyncGenerator

import fastapi.routing
from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from jararaca import (
    Get,
    HttpMicroservice,
    Microservice,
    Post,
    RestController,
    WebSocketEndpoint,
    create_http_server,
)


class Item(BaseModel):
    name: str


async def app_level_dep() -> None:
    return None


async def controller_dep() -> None:
    return None


class AppMiddleware:
    async def intercept(self) -> AsyncGenerator[None, None]:
        yield


def build_controllers() -> list[type]:
    @RestController(
        "/items",
        options={"tags": ["items"], "responses": {418: {"description": "teapot"}}},
    )
    class ItemsController:
        @Get("/")
        async def list_items(self) -> list[Item]:
            return [Item(name="a")]

        @Post("/")
        async def create_item(self, item: Item) -> Item:
            return item

        @WebSocketEndpoint("/ws")
        async def ws(self, websocket: Any) -> None:
            return None

    @RestController("/text", options={"default_response_class": PlainTextResponse})
    class TextController:
        @Get("/hello")
        async def hello(self) -> str:
            return "hello"

    return [ItemsController, TextController]


def app_factory(lifespan: Any) -> FastAPI:
    api = FastAPI(
        lifespan=lifespan,
        dependencies=[Depends(app_level_dep)],
        default_response_class=JSONResponse,
    )
    api.router.prefix = "/api"
    return api


def route_signature(route: Any) -> tuple[Any, ...]:
    if isinstance(route, APIWebSocketRoute):
        return (
            "ws",
            route.path,
            route.name,
            [d.dependency for d in route.dependencies],
        )
    assert isinstance(route, APIRoute)
    response_class = route.response_class
    return (
        route.path,
        sorted(route.methods),
        route.name,
        [d.dependency for d in route.dependencies],
        list(route.tags),
        sorted(map(str, route.responses)),
        getattr(response_class, "value", response_class),
        route.include_in_schema,
        route.unique_id,
        route.dependency_overrides_provider,
    )


def legacy_include(api: FastAPI, server: Any, controllers: list[type]) -> None:
    """What the lifespan did before: build a router per controller, then include it."""
    lifecycle = server.router.lifespan_context.lifecycle
    for controller_t in controllers:
        controller = RestController.get_last(controller_t)
        assert controller is not None
        instance = lifecycle.container.get_by_type(controller_t)
        api.include_router(controller.get_router_factory()(lifecycle, instance))


def test_routes_match_include_router_and_are_built_once(monkeypatch: Any) -> None:
    controllers = build_controllers()
    server: Any = create_http_server(
        HttpMicroservice(Microservice(controllers=controllers), app_factory)
    )

    built: list[str] = []
    original_init = fastapi.routing.APIRoute.__init__

    def counting_init(self: APIRoute, path: str, *args: Any, **kwargs: Any) -> None:
        built.append(path)
        original_init(self, path, *args, **kwargs)

    monkeypatch.setattr(fastapi.routing.APIRoute, "__init__", counting_init)

    with TestClient(server) as client:
        registered = [route_signature(r) for r in server.routes[-4:]]
        assert len(built) == 3, built

        assert client.get("/api/items/").json() == [{"name": "a"}]
        assert client.post("/api/items/", json={"name": "b"}).json() == {"name": "b"}
        response = client.get("/api/text/hello")
        assert response.text == "hello"
        assert response.headers["content-type"].startswith("text/plain")

        reference = FastAPI(
            dependencies=list(server.router.dependencies),
            default_response_class=JSONResponse,
        )
        reference.router.prefix = "/api"
        legacy_include(reference, server, controllers)
        expected = [route_signature(r) for r in reference.routes[-4:]]

    # Same routes; only the dependency_overrides_provider differs by app.
    assert [r[:-1] for r in registered] == [r[:-1] for r in expected]
    assert all(
        r[-1] is server for r in registered if r[0] != "ws"
    ), "routes must honour app.dependency_overrides"


def test_app_wide_middlewares_apply_once_to_every_controller() -> None:
    controllers = build_controllers()
    server: Any = create_http_server(
        HttpMicroservice(
            Microservice(controllers=controllers),
            app_factory,
            middlewares=[AppMiddleware],
        )
    )

    with TestClient(server):
        for route in server.routes:
            if not isinstance(route, (APIRoute, APIWebSocketRoute)):
                continue
            intercepts = [
                d
                for d in route.dependencies
                if getattr(d.dependency, "__func__", None) is AppMiddleware.intercept
            ]
            assert len(intercepts) == 1, route.path


def test_custom_router_factory_still_goes_through_include_router() -> None:
    @RestController("/custom")
    class CustomController:
        @Get("/ping")
        async def ping(self) -> str:
            return "pong"

    controller = RestController.get_last(CustomController)
    assert controller is not None

    built_in = controller.get_router_factory()
    calls: list[str] = []

    def custom_factory(lifecycle: Any, instance: Any) -> APIRouter:
        calls.append("custom")
        return built_in(lifecycle, instance)

    controller.router_factory = custom_factory

    server: Any = create_http_server(
        HttpMicroservice(Microservice(controllers=[CustomController]), app_factory)
    )
    with TestClient(server) as client:
        assert client.get("/api/custom/ping").json() == "pong"
    assert calls == ["custom"]
