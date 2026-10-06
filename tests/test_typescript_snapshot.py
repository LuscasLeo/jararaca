# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Golden-file test for the whole TypeScript generation pipeline.

Regenerate the snapshot with: UPDATE_SNAPSHOTS=1 pytest tests/test_typescript_snapshot.py
"""

import os
from enum import Enum
from pathlib import Path
from typing import Annotated, AsyncGenerator, Generic, Literal, Optional, TypeVar
from uuid import UUID

import pytest
from annotated_types import Gt
from fastapi import Depends, Header, Query, Request, UploadFile
from fastapi.params import Body, Cookie, Form
from fastapi.params import Path as PathParam
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from jararaca import Delete, Get, Microservice, Patch, Post, Put, RestController
from jararaca.presentation.decorators import UseMiddleware
from jararaca.presentation.websocket.base_types import WebSocketMessageBase
from jararaca.presentation.websocket.decorators import RegisterWebSocketMessage
from jararaca.tools.typescript.decorators import (
    ExposeType,
    MutationEndpoint,
    QueryEndpoint,
    SplitInputOutput,
)
from jararaca.tools.typescript.interface_parser import (
    write_microservice_to_typescript_interface,
    write_rest_controller_to_typescript_interface,
)

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "microservice.ts"

T = TypeVar("T")


class Color(str, Enum):
    RED = "red"
    BLUE = "blue"


class Item(BaseModel):
    id: UUID
    name: str
    color: Color
    tags: list[str] = []
    note: Optional[str] = None
    kind: Literal["a", "b"] = "a"


@SplitInputOutput()
class Profile(BaseModel):
    name: str
    age: int = 18
    nick: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int


class CreateItem(BaseModel):
    name: str
    color: Color


@ExposeType()
class Exposed(BaseModel):
    value: int


class Ping(WebSocketMessageBase):
    MESSAGE_ID = "ping"
    seq: int


class AuthMiddleware:
    async def intercept(
        self, x_token: Annotated[str, Header()]
    ) -> AsyncGenerator[None, None]:
        yield


@UseMiddleware(AuthMiddleware)
@RestController("/items")
class ItemsController:
    @Get("/")
    async def list_items(
        self, color: Annotated[Color | None, Query()] = None
    ) -> Page[Item]:
        raise NotImplementedError

    @QueryEndpoint(has_infinite_query=True)
    @Patch("/search")
    async def search_items(self, body: CreateItem) -> Page[Item]:
        raise NotImplementedError

    @QueryEndpoint()
    @MutationEndpoint()
    @Post("/both")
    async def both_item(self, body: CreateItem) -> Item:
        raise NotImplementedError

    @QueryEndpoint()
    @Get("/{item_id}")
    async def get_item(self, item_id: UUID) -> Item:
        raise NotImplementedError

    @MutationEndpoint()
    @Post("/")
    async def create_item(self, body: CreateItem) -> Item:
        raise NotImplementedError

    @Put("/{item_id}")
    async def update_item(self, item_id: UUID, body: CreateItem) -> Item:
        raise NotImplementedError

    @Patch("/profile")
    async def patch_profile(self, body: Profile) -> Profile:
        raise NotImplementedError

    @Delete("/{item_id}")
    async def delete_item(self, item_id: UUID) -> None:
        raise NotImplementedError

    @Post("/upload")
    async def upload(
        self, file: Annotated[UploadFile, Form()], title: Annotated[str, Form()]
    ) -> dict[str, int]:
        raise NotImplementedError


@RegisterWebSocketMessage(Ping)
@RestController("/ws")
class WsController:
    @Get("/status")
    async def status(self) -> Literal["ok"]:
        raise NotImplementedError


def _dependency_params(
    token: Annotated[str, Header()], session: Annotated[str, Cookie()]
) -> None:
    raise NotImplementedError


class RichMiddleware:
    async def intercept(
        self,
        request: Request,
        tenant: Annotated[str, Header()],
        trace: Annotated[str, Cookie()],
        upload: Annotated[UploadFile, Form()],
        payload: Annotated[CreateItem, Body()],
        search: Annotated[str, Query()],
        scope_id: Annotated[int, PathParam()],
        plain_path: int,
        plain_query: str,
    ) -> AsyncGenerator[None, None]:
        yield


@UseMiddleware(RichMiddleware)
@RestController("/rich/{scope_id}")
class RichController:
    @Post("/{plain_path}/mixed")
    async def mixed(
        self,
        plain_path: int,
        profile: Profile,
        item: CreateItem,
        q: str,
        flag: bool = False,
        files: list[UploadFile] = [],
        dep: Annotated[None, Depends(_dependency_params)] = None,
        cookie_value: Annotated[str, Cookie()] = "",
        header_value: Annotated[str, Header()] = "",
        profile_query: Annotated[Profile, Query()] = Profile(name="x"),
        profile_path: Annotated[Profile, PathParam()] = Profile(name="x"),
        profile_body: Annotated[Profile, Body()] = Profile(name="x"),
        form_value: Annotated[int, Form()] = 0,
    ) -> list[Profile]:
        raise NotImplementedError


PositiveId = Annotated[int, Gt(0)]


@RestController("/orgs/{org_id}")
class AliasPathController:
    """Path parameters declared with Annotated types that carry no FastAPI marker."""

    @Get("/users/{user_id}/{legacy_id}")
    async def get_user(
        self, org_id: PositiveId, user_id: PositiveId, legacy_id: UUID, page: PositiveId
    ) -> None:
        raise NotImplementedError


class GuardMiddleware:
    """Middleware using unmarked Annotated aliases and dependencies."""

    async def intercept(
        self,
        tenant_id: PositiveId,
        limit: PositiveId,
        session: Annotated[None, Depends(_dependency_params)],
        bearer: Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())],
        upload: Annotated[UploadFile, Gt(0)],
    ) -> AsyncGenerator[None, None]:
        yield


@UseMiddleware(GuardMiddleware)
@RestController("/guarded/{tenant_id}")
class GuardedController:
    @Get("/ping")
    async def ping(self) -> None:
        raise NotImplementedError


def test_microservice_typescript_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    # Other tests register types globally; keep only the ones declared in this module
    monkeypatch.setattr(ExposeType, "_exposed_types", {Exposed})
    output = write_microservice_to_typescript_interface(
        Microservice(
            controllers=[
                ItemsController,
                WsController,
                RichController,
                AliasPathController,
                GuardedController,
            ]
        )
    )

    if os.environ.get("UPDATE_SNAPSHOTS") or not SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
        SNAPSHOT_PATH.write_text(output)

    assert output == SNAPSHOT_PATH.read_text()
    assert "export class ItemsController" in output
    assert "export class WsController" in output
    assert "Exposed" in output


def test_annotated_path_parameters_without_fastapi_marker() -> None:
    """Annotated aliases named in the route are path params, not query params."""
    rest_controller = RestController.get_last(AliasPathController)
    assert rest_controller is not None

    class_buffer, _, _ = write_rest_controller_to_typescript_interface(
        rest_controller, AliasPathController
    )
    output = class_buffer.getvalue()

    path_params = output.split("pathParams: {")[1].split("}")[0]
    query_params = output.split("query: {")[1].split("}")[0]

    for name in ("org_id", "user_id", "legacy_id"):
        assert f'"{name}": {name}' in path_params
        assert f'"{name}"' not in query_params
    assert '"page": page' in query_params


def test_middleware_unmarked_annotated_and_dependencies() -> None:
    """Middleware params follow the same rules as handler params."""
    rest_controller = RestController.get_last(GuardedController)
    assert rest_controller is not None

    class_buffer, _, _ = write_rest_controller_to_typescript_interface(
        rest_controller, GuardedController
    )
    output = class_buffer.getvalue()

    assert "path: `/guarded/:tenant_id/ping`" in output
    assert '"tenant_id": tenant_id' in output.split("pathParams: {")[1].split("}")[0]
    assert '"limit": limit' in output.split("query: {")[1].split("}")[0]
    # Depends(_dependency_params) is expanded; Depends(HTTPBearer()) is not
    assert '"token": token' in output.split("headers: {")[1].split("}")[0]
    assert "bearer" not in output
    assert '"upload": upload' in output.split("makeFormData({")[1].split("}")[0]
