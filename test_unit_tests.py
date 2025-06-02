#!/usr/bin/env python3
"""
Test script to verify the enhanced HTTP RPC client functionality (unit tests)
"""

import sys
from typing import Any

# Add the src directory to the path so we can import jararaca
sys.path.insert(0, "/home/lucas-silva/workspace/personal/libraries/jararaca/src")

from jararaca.rpc.http import (
    ApiKeyAuth,
    BasicAuth,
    BearerTokenAuth,
    Body,
    CacheMiddleware,
    ContentType,
    File,
    FormData,
    Get,
    HttpRpcClientBuilder,
    HttpRPCRequest,
    HttpRPCResponse,
    HTTPXHttpRPCAsyncBackend,
    Post,
    Query,
    RestClient,
    Retry,
    RetryConfig,
    Timeout,
    TimeoutException,
)


def test_imports() -> None:
    """Test that all new classes can be imported"""
    print("✅ All enhanced HTTP RPC classes imported successfully")


def test_decorators() -> None:
    """Test that decorators can be applied to methods"""

    @RestClient("https://api.example.com")
    class TestClient:

        @Get("/users")
        @Query("limit")
        @Timeout(5.0)
        async def get_users(self, limit: int) -> dict[str, Any]:
            raise NotImplementedError(
                "This method should be implemented by the backend"
            )

        @Post("/users")
        @Body("user_data")
        @ContentType("application/json")
        @Retry(RetryConfig(max_attempts=3))
        async def create_user(self, user_data: dict[str, Any]) -> dict[str, Any]:
            raise NotImplementedError(
                "This method should be implemented by the backend"
            )

        @Post("/upload")
        @FormData("name")
        @File("avatar")
        async def upload_file(self, name: str, avatar: bytes) -> dict[str, Any]:
            raise NotImplementedError(
                "This method should be implemented by the backend"
            )

    print("✅ All decorators can be applied successfully")


def test_authentication() -> None:
    """Test authentication middleware classes"""

    # Test BearerTokenAuth
    bearer_auth = BearerTokenAuth("test-token")
    assert bearer_auth.token == "test-token"

    # Test BasicAuth
    basic_auth = BasicAuth("username", "password")
    assert basic_auth.credentials is not None  # Base64 encoded credentials

    # Test ApiKeyAuth
    api_key_auth = ApiKeyAuth("secret-key", "x-api-key")
    assert api_key_auth.header_name == "x-api-key"
    assert api_key_auth.api_key == "secret-key"

    print("✅ All authentication classes work correctly")


def test_middleware() -> None:
    """Test middleware classes"""

    # Test CacheMiddleware
    cache = CacheMiddleware(ttl_seconds=300)
    assert cache.ttl_seconds == 300
    assert isinstance(cache.cache, dict)

    print("✅ Middleware classes work correctly")


def test_configuration() -> None:
    """Test configuration classes"""

    # Test RetryConfig
    retry_config = RetryConfig(max_attempts=5, backoff_factor=2.0)
    assert retry_config.max_attempts == 5
    assert retry_config.backoff_factor == 2.0
    assert 500 in retry_config.retry_on_status_codes

    print("✅ Configuration classes work correctly")


def test_client_builder() -> None:
    """Test HttpRpcClientBuilder"""

    backend = HTTPXHttpRPCAsyncBackend()
    auth = BearerTokenAuth("test-token")
    cache = CacheMiddleware()

    builder = HttpRpcClientBuilder(backend=backend, middlewares=[auth, cache])

    assert builder._backend == backend
    assert len(builder._middlewares) == 2

    print("✅ HttpRpcClientBuilder works correctly")


def test_data_structures() -> None:
    """Test data structure classes"""

    # Test HttpRPCRequest
    request = HttpRPCRequest(
        url="https://api.example.com/users",
        method="GET",
        headers=[("Authorization", "Bearer token")],
        query_params={"limit": "10"},
        body=None,
    )

    assert request.url == "https://api.example.com/users"
    assert request.method == "GET"
    assert request.headers[0] == ("Authorization", "Bearer token")

    # Test HttpRPCResponse
    response = HttpRPCResponse(
        data=b'{"users": []}',
        status_code=200,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert (
        response.headers is not None
        and response.headers["Content-Type"] == "application/json"
    )

    print("✅ Data structures work correctly")


def test_exceptions() -> None:
    """Test exception classes"""

    # Test TimeoutException
    try:
        raise TimeoutException("Request timed out")
    except TimeoutException as e:
        assert str(e) == "Request timed out"

    print("✅ Exception classes work correctly")


def run_all_tests() -> None:
    """Run all unit tests"""
    print("🧪 Running enhanced HTTP RPC client tests...\n")

    test_imports()
    test_decorators()
    test_authentication()
    test_middleware()
    test_configuration()
    test_client_builder()
    test_data_structures()
    test_exceptions()

    print("\n🎉 All tests passed! Enhanced HTTP RPC client is working correctly.")


if __name__ == "__main__":
    run_all_tests()
