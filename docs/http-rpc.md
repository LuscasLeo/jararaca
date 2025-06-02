# HTTP RPC Client

The Jararaca HTTP RPC client provides a complete REST client implementation with a decorator-based approach for defining HTTP endpoints. It includes advanced features like authentication, caching, retry logic, form data handling, and file uploads.

## Quick Start

```python
from jararaca.rpc.http import (
    BearerTokenAuth,
    Body,
    CacheMiddleware,
    Delete,
    File,
    FormData,
    Get,
    HttpRpcClientBuilder,
    HTTPXHttpRPCAsyncBackend,
    Post,
    Put,
    Query,
    RestClient,
    Retry,
    RetryConfig,
)


@RestClient("https://api.example.com")
class ApiClient:

    @Get("/users")
    @Query("limit")
    async def get_users(self, limit: int) -> dict:
        pass

    @Post("/users")
    @Body("user_data")
    async def create_user(self, user_data: dict) -> dict:
        pass

# Create client
backend = HTTPXHttpRPCAsyncBackend()
auth = BearerTokenAuth("your-token")
cache = CacheMiddleware(ttl_seconds=300)

builder = HttpRpcClientBuilder(
    backend=backend,
    middlewares=[auth, cache]
)

client = builder.build(ApiClient)

# Use client
users = await client.get_users(10)
new_user = await client.create_user({"name": "John", "email": "john@example.com"})
```

## HTTP Method Decorators

### Basic HTTP Methods

```python
from jararaca.rpc.http import Delete, Get, Patch, Post, Put


@RestClient("https://api.example.com")
class ApiClient:

    @Get("/users")
    async def get_users(self) -> list[dict]:
        pass

    @Post("/users")
    async def create_user(self) -> dict:
        pass

    @Put("/users/{user_id}")
    async def update_user(self) -> dict:
        pass

    @Patch("/users/{user_id}")
    async def patch_user(self) -> dict:
        pass

    @Delete("/users/{user_id}")
    async def delete_user(self) -> bool:
        pass
```

## Request Parameter Decorators

### Query Parameters

```python
from jararaca.rpc.http import Query


@Get("/users")
@Query("limit")
@Query("offset")
async def get_users(self, limit: int, offset: int = 0) -> list[dict]:
    pass

# Usage: client.get_users(10, 20) -> GET /users?limit=10&offset=20
```

### Path Parameters

```python
from jararaca.rpc.http import PathParam


@Get("/users/{user_id}")
@PathParam("user_id")
async def get_user(self, user_id: int) -> dict:
    pass

# Usage: client.get_user(123) -> GET /users/123
```

### Headers

```python
from jararaca.rpc.http import Header


@Get("/users")
@Header("X-Client-Version")
async def get_users(self, x_client_version: str = "1.0") -> list[dict]:
    pass

# Usage: client.get_users("2.0") -> adds X-Client-Version: 2.0 header
```

### Request Body

```python
from jararaca.rpc.http import Body


@Post("/users")
@Body("user_data")
async def create_user(self, user_data: dict) -> dict:
    pass

# Usage: client.create_user({"name": "John"}) -> sends JSON body
```

### Form Data

```python
from jararaca.rpc.http import FormData


@Post("/login")
@FormData("username")
@FormData("password")
async def login(self, username: str, password: str) -> dict:
    pass

# Usage: client.login("user", "pass") -> sends form-encoded data
```

### File Uploads

```python
from jararaca.rpc.http import File, FormData


@Post("/upload")
@FormData("name")
@File("avatar")
async def upload_avatar(self, name: str, avatar: bytes) -> dict:
    pass

# Usage:
# with open("avatar.jpg", "rb") as f:
#     result = await client.upload_avatar("John", f.read())
```

## Configuration Decorators

### Timeout

```python
from jararaca.rpc.http import Timeout


@Get("/slow-endpoint")
@Timeout(30.0)  # 30 seconds timeout
async def slow_request(self) -> dict:
    pass
```

### Retry Configuration

```python
from jararaca.rpc.http import Retry, RetryConfig


@Get("/unreliable-endpoint")
@Retry(RetryConfig(
    max_attempts=3,
    backoff_factor=2.0,
    retry_on_status_codes=[500, 502, 503, 504]
))
async def unreliable_request(self) -> dict:
    pass
```

### Content Type

```python
from jararaca.rpc.http import ContentType


@Post("/xml-endpoint")
@ContentType("application/xml")
@Body("xml_data")
async def send_xml(self, xml_data: str) -> dict:
    pass
```

## Authentication

### Bearer Token Authentication

```python
from jararaca.rpc.http import BearerTokenAuth

auth = BearerTokenAuth("your-access-token")
builder = HttpRpcClientBuilder(backend=backend, middlewares=[auth])
```

### Basic Authentication

```python
from jararaca.rpc.http import BasicAuth

auth = BasicAuth("username", "password")
builder = HttpRpcClientBuilder(backend=backend, middlewares=[auth])
```

### API Key Authentication

```python
from jararaca.rpc.http import ApiKeyAuth

auth = ApiKeyAuth("your-api-key", header_name="X-API-Key")
builder = HttpRpcClientBuilder(backend=backend, middlewares=[auth])
```

## Middleware

### Cache Middleware

The cache middleware provides in-memory caching for GET requests:

```python
from jararaca.rpc.http import CacheMiddleware

cache = CacheMiddleware(ttl_seconds=300)  # Cache for 5 minutes
builder = HttpRpcClientBuilder(backend=backend, middlewares=[cache])
```

### Custom Request Middleware

```python
from jararaca.rpc.http import HttpRPCRequest, RequestMiddleware


class LoggingMiddleware(RequestMiddleware):
    def on_request(self, request: HttpRPCRequest) -> HttpRPCRequest:
        print(f"Making request to {request.url}")
        return request

logging_middleware = LoggingMiddleware()
builder = HttpRpcClientBuilder(backend=backend, middlewares=[logging_middleware])
```

### Response Middleware

```python
from jararaca.rpc.http import HttpRPCRequest, HttpRPCResponse, ResponseMiddleware


class ResponseLoggingMiddleware(ResponseMiddleware):
    def on_response(self, request: HttpRPCRequest, response: HttpRPCResponse) -> HttpRPCResponse:
        print(f"Response from {request.url}: {response.status_code}")
        return response

response_middleware = ResponseLoggingMiddleware()
builder = HttpRpcClientBuilder(
    backend=backend,
    response_middlewares=[response_middleware]
)
```

## Hooks

### Request Hooks

```python
from jararaca.rpc.http import HttpRPCRequest, RequestHook


class RequestTimingHook(RequestHook):
    def before_request(self, request: HttpRPCRequest) -> HttpRPCRequest:
        request.start_time = time.time()
        return request

timing_hook = RequestTimingHook()
builder = HttpRpcClientBuilder(
    backend=backend,
    request_hooks=[timing_hook]
)
```

### Response Hooks

```python
from jararaca.rpc.http import HttpRPCRequest, HttpRPCResponse, ResponseHook


class ResponseTimingHook(ResponseHook):
    def after_response(self, request: HttpRPCRequest, response: HttpRPCResponse) -> HttpRPCResponse:
        if hasattr(request, 'start_time'):
            elapsed = time.time() - request.start_time
            print(f"Request took {elapsed:.2f} seconds")
        return response

timing_hook = ResponseTimingHook()
builder = HttpRpcClientBuilder(
    backend=backend,
    response_hooks=[timing_hook]
)
```

## Error Handling

### Global Error Handlers

```python
from jararaca.rpc.http import GlobalHttpErrorHandler


@GlobalHttpErrorHandler(404)
def handle_not_found(request, response):
    return {"error": "Resource not found"}

@GlobalHttpErrorHandler(500)
def handle_server_error(request, response):
    return {"error": "Server error occurred"}
```

### Route-Specific Error Handlers

```python
from jararaca.rpc.http import RouteHttpErrorHandler


@Get("/users/{user_id}")
@PathParam("user_id")
@RouteHttpErrorHandler(404)
def handle_user_not_found(request, response):
    return {"error": f"User not found"}
async def get_user(self, user_id: int) -> dict:
    pass
```

## Advanced Features

### Complete Example with All Features

```python
import asyncio

from jararaca.rpc.http import (
    ApiKeyAuth,
    BasicAuth,
    BearerTokenAuth,
    Body,
    CacheMiddleware,
    ContentType,
    Delete,
    File,
    FormData,
    Get,
    GlobalHttpErrorHandler,
    Header,
    HttpRpcClientBuilder,
    HTTPXHttpRPCAsyncBackend,
    PathParam,
    Post,
    Put,
    Query,
    RequestHook,
    ResponseHook,
    ResponseMiddleware,
    RestClient,
    Retry,
    RetryConfig,
    RouteHttpErrorHandler,
    Timeout,
)


# Custom middleware
class RequestIdMiddleware(RequestMiddleware):
    def on_request(self, request: HttpRPCRequest) -> HttpRPCRequest:
        import uuid
        request.headers.append(("X-Request-ID", str(uuid.uuid4())))
        return request

# Error handlers
@GlobalHttpErrorHandler(500)
def handle_server_error(request, response):
    return {"error": "Server error", "status": 500}

@RestClient("https://api.example.com/v1")
class AdvancedApiClient:

    @Get("/users")
    @Query("limit")
    @Query("search")
    @Header("X-Client-Version")
    @Timeout(10.0)
    @CacheMiddleware(ttl_seconds=60)
    async def search_users(
        self,
        limit: int = 10,
        search: str = "",
        x_client_version: str = "1.0"
    ) -> list[dict]:
        pass

    @Post("/users")
    @Body("user_data")
    @ContentType("application/json")
    @Retry(RetryConfig(max_attempts=3, backoff_factor=1.5))
    @RouteHttpErrorHandler(400)
    def handle_validation_error(request, response):
        return {"error": "Validation failed", "details": response.data}
    async def create_user(self, user_data: dict) -> dict:
        pass

    @Put("/users/{user_id}/avatar")
    @PathParam("user_id")
    @File("avatar")
    @FormData("description")
    @Timeout(30.0)
    async def upload_user_avatar(
        self,
        user_id: int,
        avatar: bytes,
        description: str = ""
    ) -> dict:
        pass

    @Delete("/users/{user_id}")
    @PathParam("user_id")
    @Retry(RetryConfig(max_attempts=2))
    async def delete_user(self, user_id: int) -> bool:
        pass

async def main():
    # Setup backend and middleware
    backend = HTTPXHttpRPCAsyncBackend(default_timeout=15.0)
    auth = BearerTokenAuth("your-access-token")
    cache = CacheMiddleware(ttl_seconds=300)
    request_id = RequestIdMiddleware()

    # Build client with all features
    builder = HttpRpcClientBuilder(
        backend=backend,
        middlewares=[auth, cache, request_id],
        response_middlewares=[],
        request_hooks=[],
        response_hooks=[]
    )

    client = builder.build(AdvancedApiClient)

    try:
        # Use the client
        users = await client.search_users(limit=20, search="john")
        new_user = await client.create_user({
            "name": "Jane Doe",
            "email": "jane@example.com"
        })

        # Upload avatar
        with open("avatar.jpg", "rb") as f:
            avatar_result = await client.upload_user_avatar(
                user_id=new_user["id"],
                avatar=f.read(),
                description="Profile picture"
            )

        print("✅ All operations completed successfully")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
```

## Backend Configuration

### HTTPX Backend Options

```python
from jararaca.rpc.http import HTTPXHttpRPCAsyncBackend

backend = HTTPXHttpRPCAsyncBackend(
    prefix_url="https://api.example.com",  # Base URL for all requests
    default_timeout=30.0  # Default timeout in seconds
)
```

## Exception Handling

The HTTP RPC client provides several exception types:

- `TimeoutException`: Raised when a request times out
- `RPCRequestNetworkError`: Raised for network-related errors
- `RPCUnhandleError`: Raised when no error handler matches the response status

```python
from jararaca.rpc.http import RPCRequestNetworkError, RPCUnhandleError, TimeoutException

try:
    result = await client.get_users()
except TimeoutException:
    print("Request timed out")
except RPCRequestNetworkError:
    print("Network error occurred")
except RPCUnhandleError as e:
    print(f"Unhandled error: {e.response.status_code}")
```

## Best Practices

1. **Use Type Hints**: Always provide type hints for better IDE support and documentation
2. **Error Handling**: Implement appropriate error handlers for expected error conditions
3. **Timeouts**: Set reasonable timeouts for all requests
4. **Retry Logic**: Use retry configuration for operations that may fail temporarily
5. **Caching**: Use cache middleware for read-heavy operations
6. **Authentication**: Store tokens securely and refresh them as needed
7. **Middleware Order**: Consider the order of middleware execution
8. **Resource Management**: Use async context managers when appropriate

## Migration from Previous Versions

If you're upgrading from a previous version of the HTTP RPC client, here are the key changes:

1. **New Decorators**: `@FormData`, `@File`, `@Timeout`, `@Retry`, `@ContentType`
2. **Authentication**: New authentication middleware classes
3. **Caching**: Built-in cache middleware
4. **Enhanced Error Handling**: More granular exception types
5. **Middleware System**: Expanded middleware and hooks system
6. **Form Data Support**: Native support for form submissions and file uploads

All existing functionality remains backward compatible.
