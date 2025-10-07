"""
Integration tests for the complete Jararaca framework.
"""

import pytest
from pydantic import BaseModel

from jararaca import (
    ExposeType,
    Get,
    HttpMicroservice,
    Microservice,
    Post,
    RestController,
    SplitInputOutput,
    create_http_server,
)
from jararaca.core.providers import ProviderSpec, Token


@pytest.mark.integration
class TestMicroserviceIntegration:
    """Integration tests for complete microservice setup."""

    def test_complete_microservice_with_exposed_types(self) -> None:
        """Test a complete microservice with @ExposeType integration."""

        @ExposeType()
        class UserPermission(BaseModel):
            resource: str
            action: str

        @ExposeType()
        @SplitInputOutput()
        class User(BaseModel):
            id: str
            username: str
            email: str

        class UserListResponse(BaseModel):
            users: list[User]
            total: int

        @RestController("/api/users")
        class UserController:
            @Get("/")
            async def list_users(self) -> UserListResponse:
                return UserListResponse(users=[], total=0)

            @Post("/")
            async def create_user(self, user: User) -> User:
                return user

        app = Microservice(
            providers=[],
            controllers=[UserController],
            interceptors=[],
        )

        # Verify exposed types are tracked
        exposed = ExposeType.get_all_exposed_types()
        assert UserPermission in exposed
        assert User in exposed

        # Verify microservice structure
        assert len(app.controllers) == 1
        assert app.controllers[0] == UserController

    def test_microservice_with_dependency_injection(self) -> None:
        """Test microservice with dependency injection."""

        class UserService:
            def get_users(self) -> list[str]:
                return ["user1", "user2"]

        @RestController("/api/users")
        class UserController:
            def __init__(self, user_service: UserService):
                self.user_service = user_service

            @Get("/")
            async def list_users(self) -> list[str]:
                return self.user_service.get_users()

        service = UserService()
        app = Microservice(
            providers=[ProviderSpec(provide=UserService, use_value=service)],
            controllers=[UserController],
            interceptors=[],
        )

        assert len(app.providers) == 1
        assert len(app.controllers) == 1

    def test_microservice_with_token_based_injection(self) -> None:
        """Test microservice with token-based dependency injection."""

        class MyService:
            def do_something(self) -> str:
                return "done"

        token = Token(MyService, "MY_SERVICE")
        service = MyService()

        @RestController("/api")
        class TestController:
            @Get("/")
            async def index(self) -> dict[str, str]:
                return {"status": "ok"}

        app = Microservice(
            providers=[ProviderSpec(provide=token, use_value=service)],
            controllers=[TestController],
            interceptors=[],
        )

        assert len(app.providers) == 1
        provider = app.providers[0]
        assert provider.provide == token
        assert provider.use_value == service

    def test_http_microservice_creation(self) -> None:
        """Test creating an HTTP microservice."""

        @RestController("/api")
        class TestController:
            @Get("/health")
            async def health_check(self) -> dict[str, str]:
                return {"status": "healthy"}

        app = Microservice(
            providers=[],
            controllers=[TestController],
            interceptors=[],
        )

        http_app = HttpMicroservice(app)
        assert http_app.app == app

        # Create the server
        server = create_http_server(http_app)
        assert server is not None
