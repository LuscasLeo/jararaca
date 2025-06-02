#!/usr/bin/env python3
"""
Test script to verify the enhanced HTTP RPC client functionality
"""

import asyncio
import sys
from typing import Any

# Add the src directory to the path so we can import jararaca
sys.path.insert(0, "/home/lucas-silva/workspace/personal/libraries/jararaca/src")

from jararaca.rpc.http import (
    BearerTokenAuth,
    Body,
    CacheMiddleware,
    ContentType,
    FormData,
    Get,
    HttpRpcClientBuilder,
    HTTPXHttpRPCAsyncBackend,
    Post,
    Query,
    RestClient,
    Retry,
    RetryConfig,
    Timeout,
)


@RestClient("https://httpbin.org")
class HttpBinClient:
    """Test client for httpbin.org"""

    @Get("/get")
    @Query("test_param")
    @Timeout(5.0)
    async def get_with_query(self, test_param: str) -> dict[str, Any]:
        raise NotImplementedError("This method should be implemented by the backend")

    @Post("/post")
    @Body("data")
    @ContentType("application/json")
    @Retry(RetryConfig(max_attempts=2))
    async def post_json(self, data: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("This method should be implemented by the backend")

    @Post("/post")
    @FormData("username")
    @FormData("password")
    async def post_form(self, username: str, password: str) -> dict[str, Any]:
        raise NotImplementedError("This method should be implemented by the backend")


async def test_client() -> None:
    """Test the enhanced HTTP RPC client"""

    # Create backend
    backend = HTTPXHttpRPCAsyncBackend()

    # Create middlewares
    auth = BearerTokenAuth("test-token")
    cache = CacheMiddleware(ttl_seconds=300)

    # Build client
    builder = HttpRpcClientBuilder(backend=backend, middlewares=[auth, cache])

    # Create client instance
    client = builder.build(HttpBinClient)

    try:
        # Test GET with query parameters
        print("Testing GET with query...")
        result = await client.get_with_query("hello-world")
        print(f"GET result: {result.get('args', {})}")

        # Test POST with JSON body
        print("\nTesting POST with JSON...")
        json_data = {"message": "hello", "number": 42}
        result = await client.post_json(json_data)
        print(f"POST result: {result.get('json', {})}")

        # Test POST with form data
        print("\nTesting POST with form data...")
        result = await client.post_form("testuser", "testpass")
        print(f"Form result: {result.get('form', {})}")

        print("\n✅ All tests passed!")

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_client())
