# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for REST controller decorators.
"""

from jararaca import Get, Post, RestController
from jararaca.presentation.decorators import HttpMapping


class TestRestControllerDecorator:
    """Test suite for @RestController decorator."""

    def test_rest_controller_basic(self) -> None:
        """Test basic @RestController decorator."""

        @RestController("/api/test")
        class TestController:
            pass

        rest_controller = RestController.get_last(TestController)
        assert rest_controller is not None
        assert rest_controller.path == "/api/test"

    def test_rest_controller_with_options(self) -> None:
        """Test @RestController with options."""

        @RestController("/api/test", options={"tags": ["test"]})
        class TestController:
            pass

        rest_controller = RestController.get_last(TestController)
        assert rest_controller is not None
        assert rest_controller.options == {"tags": ["test"]}

    def test_rest_controller_returns_none_for_undecorated(self) -> None:
        """Test that get_controller returns None for undecorated classes."""

        class NotAController:
            pass

        assert RestController.get_last(NotAController) is None


class TestHttpMethodDecorators:
    """Test suite for HTTP method decorators (@Get, @Post, etc.)."""

    def test_get_decorator(self) -> None:
        """Test @Get decorator."""

        @RestController("/api")
        class TestController:
            @Get("/items")
            async def get_items(self) -> list[str]:
                return ["item1", "item2"]

        mapping = HttpMapping.get_last(TestController.get_items)
        assert mapping is not None
        assert mapping.method == "GET"
        assert mapping.path == "/items"

    def test_post_decorator(self) -> None:
        """Test @Post decorator."""

        @RestController("/api")
        class TestController:
            @Post("/items")
            async def create_item(self) -> dict[str, str]:
                return {"status": "created"}

        mapping = HttpMapping.get_last(TestController.create_item)
        assert mapping is not None
        assert mapping.method == "POST"
        assert mapping.path == "/items"

    def test_http_mapping_with_response_type(self) -> None:
        """Test HttpMapping with response_type parameter."""

        @RestController("/api")
        class TestController:
            @Get("/download", response_type="blob")
            async def download_file(self) -> bytes:
                return b"file content"

        mapping = HttpMapping.get_last(TestController.download_file)
        assert mapping is not None
        assert mapping.response_type == "blob"

    def test_multiple_endpoints_in_controller(self) -> None:
        """Test controller with multiple endpoints."""

        @RestController("/api/users")
        class UserController:
            @Get("/")
            async def list_users(self) -> list[dict[str, str]]:
                return []

            @Get("/{user_id}")
            async def get_user(self, user_id: str) -> dict[str, str]:
                return {"id": user_id}

            @Post("/")
            async def create_user(self) -> dict[str, str]:
                return {"status": "created"}

        list_mapping = HttpMapping.get_last(UserController.list_users)
        get_mapping = HttpMapping.get_last(UserController.get_user)
        create_mapping = HttpMapping.get_last(UserController.create_user)

        assert list_mapping is not None
        assert get_mapping is not None
        assert create_mapping is not None

        assert list_mapping.method == "GET"
        assert get_mapping.method == "GET"
        assert create_mapping.method == "POST"
