# Interceptors in Jararaca

Interceptors are a powerful mechanism in Jararaca for implementing cross-cutting concerns across different execution contexts. They allow you to wrap the execution of HTTP requests, message handling, scheduled tasks, and WebSocket communications with common behaviors such as transaction management, message publishing, and WebSocket message dispatching.

## Overview of the Interceptor System

Interceptors in Jararaca work by wrapping the execution of handlers in different contexts:

- **HTTP Controllers**: Intercept incoming HTTP requests before they reach controllers
- **Message Bus Consumers**: Intercept message processing in workers
- **Scheduled Tasks**: Intercept scheduled task execution
- **WebSocket Connections**: Manage WebSocket connections and message dispatching

Each interceptor provides a specific capability to the application through context variables, allowing your code to access these capabilities through hook functions like `use_session()`, `use_publisher()`, and `use_ws_manager()`.

## The Atomic Layer: Transactions, Messages, and WebSockets

The true power of the interceptor system lies in how it creates an atomic layer that ensures consistency across database operations, message publishing, and WebSocket communications. This is particularly important for implementing transactional outbox patterns and ensuring consistency in distributed systems.

```mermaid
sequenceDiagram
    participant Client
    participant HTTP Context
    participant DB Interceptor
    participant MessageBus Interceptor
    participant WebSocket Interceptor
    participant Database
    participant MessageBroker
    participant WebSocketBackend

    Client->>HTTP Context: HTTP Request
    Note over HTTP Context,WebSocketBackend: Transaction Boundary Begins

    HTTP Context->>DB Interceptor: Begin Transaction
    DB Interceptor->>Database: Begin DB Transaction

    HTTP Context->>MessageBus Interceptor: Stage Messages
    MessageBus Interceptor->>MessageBus Interceptor: Queue Messages

    HTTP Context->>WebSocket Interceptor: Stage WebSocket Messages
    WebSocket Interceptor->>WebSocket Interceptor: Queue WebSocket Messages

    HTTP Context->>DB Interceptor: Commit Transaction
    DB Interceptor->>Database: Commit DB Transaction
    Database-->>DB Interceptor: Success

    DB Interceptor-->>MessageBus Interceptor: DB Commit Success
    MessageBus Interceptor->>MessageBroker: Flush Queued Messages

    MessageBus Interceptor-->>WebSocket Interceptor: Messages Published
    WebSocket Interceptor->>WebSocketBackend: Flush WebSocket Messages

    Note over HTTP Context,WebSocketBackend: Transaction Boundary Ends

    HTTP Context-->>Client: HTTP Response
```

### Order of Interceptors Matters

The order in which interceptors are configured in your application is critical. The typical order is:

1. **Configuration Interceptor** - Loads and provides application configuration
2. **Message Bus Publisher Interceptor** - Provides message publishing capabilities
3. **Database Session Interceptor** - Provides database transaction capabilities
4. **WebSocket Interceptor** - Provides WebSocket communication capabilities

This order ensures that:

1. The database transaction is committed first
2. Only after successful database commit, messages are published to the message broker
3. Finally, WebSocket messages are dispatched to connected clients

## Implementing Transactional Outbox Pattern

Jararaca's interceptor system makes implementing the transactional outbox pattern straightforward. This pattern ensures that database changes and message publishing are atomic:

```python
from jararaca.persistence.session import use_session
from jararaca.messagebus.publisher import use_publisher

async def create_user(user_data: dict):
    # Get the current database session from the context
    session = use_session()

    # Get the message publisher from the context
    publisher = use_publisher()

    # Create user in database
    new_user = User(**user_data)
    session.add(new_user)

    # Stage a message to be published after successful transaction
    await publisher.publish("user_created", UserCreatedEvent(id=new_user.id))

    # The actual database commit and message publishing
    # happens automatically when the request handling is complete,
    # managed by the interceptors
```

When this code executes:
1. The database session interceptor ensures the user is committed to the database
2. If the database transaction succeeds, the message bus interceptor publishes the staged message
3. If the database transaction fails, no messages are published

## Contexts and Interceptors

Jararaca supports different execution contexts, and interceptors behave differently in each:

### HTTP Context

In an HTTP context, all interceptors are active:
- Database Session Interceptor manages transactions
- Message Bus Publisher Interceptor stages and flushes messages
- WebSocket Interceptor stages and flushes WebSocket messages

### Worker Context

In a message bus worker context:
- Database Session Interceptor manages transactions
- Message Bus Publisher Interceptor stages and flushes messages
- WebSocket Interceptor stages and flushes WebSocket messages

### Scheduler Context

In a scheduler context:
- Database Session Interceptor manages transactions
- Message Bus Publisher Interceptor stages and flushes messages
- WebSocket Interceptor stages and flushes WebSocket messages

### WebSocket Context

In a WebSocket context:
- Database Session Interceptor manages transactions
- Message Bus Publisher is usually not active (as defined in the `intercept` method)
- WebSocket communications are managed directly

## Code Example: Configuring Interceptors

```python
from jararaca import Microservice
from jararaca.persistence.interceptors import AIOSqlAlchemySessionInterceptor, AIOSQAConfig
from jararaca.messagebus.interceptors import MessageBusPublisherInterceptor
from jararaca.presentation.websocket import WebSocketInterceptor

# Create the microservice
microservice = Microservice(
    name="my-service",
    interceptors=[
        # Configuration interceptor (if any)
        # ...

        # Message bus interceptor
        lambda config: MessageBusPublisherInterceptor(
            connection_factory=message_publisher_factory,
            connection_name="default",
            message_scheduler=message_scheduler,
        ),

        # Database session interceptor
        lambda config: AIOSqlAlchemySessionInterceptor(
            AIOSQAConfig(
                url="postgresql+asyncpg://user:password@localhost/dbname",
                connection_name="default",
                inject_default=True,
            )
        ),

        # WebSocket interceptor
        lambda config: WebSocketInterceptor(
            backend=redis_websocket_backend,
        ),
    ],
    # Other microservice configuration...
)
```

## Best Practices

1. **Order Matters**: Always configure interceptors in the order: configuration → message bus → database → WebSocket
2. **Transaction Boundaries**: Be aware that interceptors create implicit transaction boundaries
3. **Error Handling**: Database errors will prevent message publishing and WebSocket dispatching
4. **Idempotency**: Design your message handlers to be idempotent in case of retries
5. **Context Variables**: Use the appropriate context hooks (`use_session()`, `use_publisher()`, etc.) to access the capabilities provided by interceptors

## Advanced Usage: Custom Interceptors

You can create custom interceptors by implementing the `AppInterceptor` interface:

```python
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from jararaca.microservice import AppInterceptor, AppTransactionContext

class CustomInterceptor(AppInterceptor):
    @asynccontextmanager
    async def intercept(self, app_context: AppTransactionContext) -> AsyncGenerator[None, None]:
        # Pre-processing logic

        try:
            # Let the request handler execute
            yield
            # Post-processing after successful execution
        except Exception as e:
            # Handle exceptions
            raise e
```

## Conclusion

Jararaca's interceptor system provides a powerful way to implement cross-cutting concerns and ensure consistency across database operations, message publishing, and WebSocket communications. By properly configuring interceptors, you can implement patterns like the transactional outbox pattern with minimal effort, ensuring that your distributed system maintains consistency even in the face of failures.
