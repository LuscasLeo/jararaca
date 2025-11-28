# HTTP Request Context Management

Jararaca provides Unit of Work (UoW) context management for HTTP requests, ensuring that database transactions, message publishing, and WebSocket communications are handled atomically. This document covers how the UoW context system works and how to use it.

## Overview

The UoW context ensures that:
- Database transactions are properly scoped to requests
- Messages are only published if the database transaction succeeds (transactional outbox pattern)
- WebSocket messages are dispatched after successful database commits
- All operations within a request share the same transaction context

## Current Implementation: HttpUowContextProviderDependency

Jararaca currently implements UoW context management using FastAPI's dependency injection system through the `HttpUowContextProviderDependency` class.

```python
from fastapi import Depends
from jararaca import HttpMicroservice, create_http_server
from jararaca.presentation.server import HttpUowContextProviderDependency

# The create_http_server function automatically sets up the UoW context
http_app = HttpMicroservice(app=app)
fastapi_app = create_http_server(http_app)

# Under the hood, it does this:
uow_provider = UnitOfWorkContextProvider(app, container)
uow_dependency = HttpUowContextProviderDependency(uow_provider)

fastapi_app.router.dependencies.append(
    Depends(uow_dependency)
)
```

The `HttpUowContextProviderDependency` automatically:
1. Extracts endpoint metadata from the request
2. Sets up the UoW context with all configured interceptors
3. Manages the transaction boundary
4. Handles errors and converts them to appropriate HTTP responses

### Request Lifecycle

When a request comes in, the following happens:

1. **Request Received** → FastAPI invokes the dependency
2. **Context Setup** → `HttpUowContextProviderDependency` sets up the UoW context
3. **Interceptors Activated** → All configured interceptors are invoked in order:
   - Configuration Interceptor (if any)
   - Message Bus Publisher Interceptor (stages messages)
   - Database Session Interceptor (begins transaction)
   - WebSocket Interceptor (stages WebSocket messages)
4. **Handler Execution** → Your controller method executes
5. **Commit Phase** → Interceptors commit in reverse order:
   - Database commits first
   - Messages are published (only if DB commit succeeded)
   - WebSocket messages are dispatched
6. **Response Returned** → FastAPI sends the response

### Error Handling

The dependency automatically handles errors:

```python
try:
    # Your endpoint code executes
    yield
except HTTPException:
    # FastAPI's HTTP exceptions pass through
    raise
except Exception as e:
    # Other exceptions become 500 Internal Server Error
    logger.exception("Unhandled exception in request handling.")
    raise HTTPException(
        status_code=500,
        detail={
            "message": "Internal server error occurred.",
            "x-traceparentid": response.headers.get("traceparent")
        }
    ) from e
```

## WebSocket Support

The same system works for WebSocket connections:

```python
@RestController("/ws")
class WebSocketController:
    @WebSocketEndpoint("/chat")
    async def chat_endpoint(self, websocket: WebSocket) -> None:
        # The UoW context is automatically available
        # You can use use_session(), use_publisher(), etc.
        pass
```

The `HttpUowContextProviderDependency` detects whether it's handling an HTTP request or WebSocket connection and sets up the appropriate context.

## Usage in Controllers

Once the UoW context is set up, you can use context hooks anywhere in your code:

```python
from jararaca import RestController, Post, use_session, use_publisher

@RestController("/api/users")
class UserController:
    @Post("/")
    async def create_user(self, user_data: dict) -> dict:
        # Access the database session
        session = use_session()

        # Create user
        user = User(**user_data)
        session.add(user)

        # Stage a message for publishing
        publisher = use_publisher()
        await publisher.publish(UserCreatedEvent(user_id=user.id))

        # Transaction commits and message publishes automatically
        return user.to_dict()
```

## Integration with Interceptors

The UoW context works seamlessly with all interceptors. See the [Interceptors](interceptors.md) documentation for details on:

- How interceptor order affects transaction boundaries
- Implementing custom interceptors
- Transactional outbox pattern
- Message staging and flushing

## Complete Example

Here's a complete example showing how to set up a microservice with UoW context management:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from jararaca import (
    AIOSQAConfig,
    AIOSqlAlchemySessionInterceptor,
    HttpMicroservice,
    MessageBusPublisherInterceptor,
    Microservice,
    WebSocketInterceptor,
)
from jararaca.presentation.server import create_http_server


def fastapi_factory(lifespan):
    """Custom FastAPI factory with CORS."""
    app = FastAPI(lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return app

# Create microservice with interceptors
app = Microservice(
    name="my-service",
    interceptors=[
        # Message bus for event publishing
        MessageBusPublisherInterceptor(...),

        # Database sessions
        AIOSqlAlchemySessionInterceptor(
            AIOSQAConfig(
                connection_name="default",
                url="postgresql+asyncpg://user:pass@localhost/mydb",
                inject_default=True,
            )
        ),

        # WebSocket management
        WebSocketInterceptor(...),
    ],
    controllers=[UserController, OrderController],
)

# Create HTTP server - UoW context is automatically configured
http_app = HttpMicroservice(app=app, factory=fastapi_factory)
fastapi_app = create_http_server(http_app)
```

## Best Practices

1. **Use Context Hooks**: Always access resources via `use_session()`, `use_publisher()`, etc.
2. **Let Interceptors Manage Transactions**: Don't manually commit/rollback unless necessary
3. **Order Interceptors Correctly**: Configuration → Message Bus → Database → WebSocket
4. **Handle Errors Gracefully**: Let the dependency convert exceptions to HTTP responses
5. **Test with Mocked Context**: Use fixtures to provide test contexts

## Advanced: Manual Context Management

In rare cases, you might need to manually manage the UoW context (e.g., in background tasks):

```python
from jararaca import AppTransactionContext, Container
from jararaca.core.uow import UnitOfWorkContextProvider

async def background_task():
    """Execute a background task with UoW context."""
    container = Container(app)
    uow_provider = UnitOfWorkContextProvider(app, container)

    # Create transaction context manually
    async with uow_provider(
        AppTransactionContext(
            controller_member_reflect=member_reflect,
            transaction_data=transaction_data,
        )
    ):
        # Your background task code
        session = use_session()
        # ... perform operations
```

**Note**: This is rarely needed as most operations should happen within request handlers where the context is automatically provided.

## Troubleshooting

### "No session set in the context"

This error means you're trying to use `use_session()` outside of a UoW context. Ensure:
- You're calling it from within a controller method
- The `HttpUowContextProviderDependency` is properly configured
- You're not trying to use it in module-level code

### Transactions Not Committing

If changes aren't being persisted:
- Check that no exceptions are being raised
- Verify interceptor order is correct
- Ensure you're not manually rolling back the transaction

### Messages Not Publishing

If messages aren't being published:
- Verify the database transaction succeeded
- Check that the Message Bus interceptor is configured before the Database interceptor
- Review logs for publishing errors

## See Also

- [Interceptors](interceptors.md) - Detailed information on the interceptor system
- [Persistence](persistence.md) - Database and session management
- [Message Bus](messagebus.md) - Event publishing and handling
- [WebSocket](websocket.md) - WebSocket communication
- [Metadata](metadata.md) - Metadata system and custom decorators
