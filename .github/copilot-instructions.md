# Jararaca Framework Development Guide

## Architecture Overview

Jararaca is an async-first microservice framework built on **unified runtime interfaces**. The same `Microservice` declaration runs in three separate process types:
- **HTTP Server** (`jararaca server`) - REST APIs and WebSocket endpoints
- **Worker** (`jararaca worker`) - Message bus consumers for async tasks/events
- **Scheduler** (`jararaca scheduler`) - Cron-based scheduled jobs

The core innovation: utilities like `use_session()`, `use_publisher()`, and `use_ws_manager()` work identically across all runtimes via context variables.

## Project Structure

```
src/jararaca/
├── microservice.py         # Core Microservice class, transaction context system
├── di.py                   # Container-based dependency injection (aliased from microservice.py)
├── lifecycle.py            # AppLifecycle manager for interceptor initialization
├── presentation/           # HTTP/REST controllers (@RestController, @Get, @Post, etc.)
├── messagebus/            # Event/task system (@MessageBusController, @MessageHandler)
├── scheduler/             # Cron scheduling (@ScheduledAction)
├── rpc/http/              # HTTP RPC client library (decorators for REST clients)
├── persistence/           # SQLAlchemy integration (BaseEntity, session management)
├── observability/         # OpenTelemetry integration
└── tools/typescript/      # TypeScript interface generation from Python types
```

## Controller Patterns

### REST Controllers
```python
@RestController("/api/users", middlewares=[AuthMiddleware])
class UserController:
    def __init__(self, user_service: UserService):
        self.user_service = user_service

    @Get("/{user_id}")
    async def get_user(self, user_id: str) -> UserResponse:
        return await self.user_service.get(user_id)
```

### Message Bus Controllers
```python
@MessageBusController()
class UserEventsController:
    @MessageHandler(UserCreatedEvent, auto_ack=True)
    async def handle_user_created(self, message: MessageOf[UserCreatedEvent]) -> None:
        # Process event
        pass
```

### Scheduled Actions
```python
@MessageBusController()
class ScheduledTasksController:
    @ScheduledAction(cron="0 * * * *")  # Every hour
    async def cleanup_old_data(self) -> None:
        # Task logic
        pass
```

## Dependency Injection

Use `ProviderSpec` with `Token` for explicit DI:
```python
app = Microservice(
    providers=[
        ProviderSpec(
            provide=Token(HelloRPC, "HELLO_RPC"),
            use_value=HttpRpcClientBuilder(...).build(HelloRPC)
        )
    ],
    controllers=[MyController],
    interceptors=[...]
)
```

Controllers receive dependencies via constructor injection:
```python
class MyController:
    def __init__(self, hello_rpc: Annotated[HelloRPC, Token(HelloRPC, "HELLO_RPC")]):
        self.hello_rpc = hello_rpc
```

## Interceptor System (Critical)

**Order matters!** Interceptors execute in declaration order and create an atomic transaction boundary:

1. Configuration Interceptor → loads config
2. Message Bus Interceptor → stages messages for publishing
3. Database Session Interceptor → provides transaction
4. WebSocket Interceptor → stages WebSocket broadcasts

**Commit flow**: DB commits first → messages published → WebSocket messages dispatched. This implements the transactional outbox pattern automatically.

Access interceptor capabilities via context hooks:
- `use_session()` - SQLAlchemy async session
- `use_publisher()` - Message publisher (publishes after DB commit)
- `use_ws_manager()` - WebSocket manager for room broadcasts

## CLI Commands

```bash
# Run HTTP server
jararaca server app:http_app

# Run message bus worker (RabbitMQ)
jararaca worker app:app --broker-url "amqp://user:pass@host:5672/?exchange=my_exchange"

# Run scheduler
jararaca scheduler app:app --broker-url "amqp://..."

# Declare RabbitMQ infrastructure (exchanges/queues)
jararaca declare-worker-infra app:app --broker-url "amqp://..." --force

# Generate TypeScript interfaces from Python types
jararaca gen-tsi app:app output.ts
```

## HTTP RPC Client Pattern

Build type-safe REST clients using decorators:
```python
@RestClient
class UserAPI:
    @Get("/users/{user_id}")
    async def get_user(self, user_id: Annotated[str, PathParam()]) -> UserResponse:
        ...

# Build client
client = HttpRpcClientBuilder(
    HTTPXHttpRPCAsyncBackend(prefix_url="https://api.example.com"),
    middlewares=[TracedRequestMiddleware()]
).build(UserAPI)
```

## WebSocket Endpoints

```python
@RestController("/ws")
class WebSocketController:
    @WebSocketEndpoint("/chat")
    @RegisterWebSocketMessage(ChatMessage, ChatResponse)
    async def chat_endpoint(self, websocket: WebSocket) -> None:
        # WebSocket handler
        pass
```

## Database Patterns

Entities inherit from `BaseEntity` (SQLAlchemy 2.0 + async):
```python
class User(BaseEntity):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
```

Use `from_basemodel()` and `to_basemodel()` for Pydantic conversion.

## TypeScript Generation

Decorators control TypeScript output:
- `@QueryEndpoint` → generates GraphQL-style query
- `@MutationEndpoint` → generates mutation
- `@SplitInputOutput` → splits Pydantic models into separate input/output types

## Development Workflow

- **Poetry** for dependency management (`poetry install`)
- **Makefile** for releases (`make release bump-type=minor`)
- **MkDocs** for documentation (`mkdocs serve`)
- No test infrastructure detected - framework expects users to add their own
- Uses **mypy strict mode** - all code must be fully typed

## Key Conventions

- All async operations (async/await throughout)
- Use `Token(Type, "NAME")` for explicit provider tokens
- Controllers are registered by class, not instance
- Message topics auto-derived from `IMessage.MESSAGE_TOPIC` class variable
- WebSocket rooms managed via distributed Redis backend
- OpenTelemetry integration via `ObservabilityInterceptor` + `@TracedFunc`
