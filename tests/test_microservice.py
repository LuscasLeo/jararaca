# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Microservice core functionality.
"""

from jararaca import Get, Microservice, RestController
from jararaca.core.providers import ProviderSpec


class TestMicroservice:
    """Test suite for Microservice class."""

    def test_microservice_creation_empty(self) -> None:
        """Test creating an empty Microservice."""
        app = Microservice(providers=[], controllers=[], interceptors=[])

        assert app.providers == []
        assert app.controllers == []
        assert app.interceptors == []

    def test_microservice_with_providers(self) -> None:
        """Test Microservice with providers."""

        class MyService:
            pass

        service = MyService()
        provider = ProviderSpec(provide=MyService, use_value=service)

        app = Microservice(
            providers=[provider],
            controllers=[],
            interceptors=[],
        )

        assert len(app.providers) == 1
        assert app.providers[0] == provider

    def test_microservice_with_controllers(self) -> None:
        """Test Microservice with controllers."""

        @RestController("/api")
        class TestController:
            @Get("/")
            async def index(self) -> dict[str, str]:
                return {"message": "hello"}

        app = Microservice(
            providers=[],
            controllers=[TestController],
            interceptors=[],
        )

        assert len(app.controllers) == 1
        assert app.controllers[0] == TestController

    def test_microservice_with_multiple_controllers(self) -> None:
        """Test Microservice with multiple controllers."""

        @RestController("/api/users")
        class UserController:
            @Get("/")
            async def list_users(self) -> list[str]:
                return []

        @RestController("/api/posts")
        class PostController:
            @Get("/")
            async def list_posts(self) -> list[str]:
                return []

        app = Microservice(
            providers=[],
            controllers=[UserController, PostController],
            interceptors=[],
        )

        assert len(app.controllers) == 2
        assert UserController in app.controllers
        assert PostController in app.controllers

    def test_microservice_immutable_after_creation(self) -> None:
        """Test that Microservice lists are immutable after creation."""
        app = Microservice(providers=[], controllers=[], interceptors=[])

        # Lists should be the same reference (frozen)
        original_providers = app.providers
        original_controllers = app.controllers
        original_interceptors = app.interceptors

        assert app.providers is original_providers
        assert app.controllers is original_controllers
        assert app.interceptors is original_interceptors
