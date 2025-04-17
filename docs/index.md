# Jararaca Microservice Framework

Jararaca is a powerful Python microservice framework that provides a comprehensive set of tools and abstractions for building robust microservice architectures. It integrates seamlessly with FastAPI, SQLAlchemy, Redis, and RabbitMQ to deliver a complete solution for modern microservice development.

## Features

- 🚀 **FastAPI Integration**: Built-in support for FastAPI with automatic OpenAPI documentation
- 🔌 **WebSocket Support**: Real-time communication capabilities with Redis-backed WebSocket management
- 📦 **Dependency Injection**: Flexible dependency injection system with interceptors
- 📊 **Database Integration**: SQLAlchemy integration with async support
- 📡 **Message Bus**: RabbitMQ integration for event-driven architecture
- 🔒 **Authentication**: Built-in JWT authentication with token blacklisting
- 🔍 **Query Operations**: Advanced query capabilities with pagination and filtering
- ⏱️ **Scheduled Tasks**: Cron-based task scheduling
- 🛠️ **CRUD Operations**: Simplified database operations with automatic entity mapping

## Installation

```bash
pip install jararaca
```

## CLI Commands

Jararaca comes with a powerful command-line interface to help you manage your microservices:

### `worker` - Message Bus Worker

```bash
jararaca worker APP_PATH [OPTIONS]
```

Starts a message bus worker that processes asynchronous messages from a message queue.

**Options:**

- `--url`: AMQP URL (default: "amqp://guest:guest@localhost/")
- `--username`: AMQP username (optional)
- `--password`: AMQP password (optional)
- `--exchange`: Exchange name (default: "jararaca_ex")
- `--queue`: Queue name (default: "jararaca_q")
- `--prefetch-count`: Number of messages to prefetch (default: 1)

### `server` - HTTP Server

```bash
jararaca server APP_PATH [OPTIONS]
```

#### Perfer `uvicorn` for production

Starts a FastAPI HTTP server for your microservice.

```python
def fastapi_factory(lifespan: Lifespan[FastAPI]) -> FastAPI:
    app = FastAPI(
        lifespan=lifespan,
    )

    app.router.prefix = "/api"

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["error", "reason", "scope"],
    )

    return app


http_app = HttpMicroservice(app, fastapi_factory)

asgi_app = create_http_server(http_app)
```

Then run the server with:

```bash
uvicorn app_module:asgi_app
```

Starts a FastAPI HTTP server for your microservice.

### `scheduler` - Task Scheduler

```bash
jararaca scheduler APP_PATH [OPTIONS]
```

Runs scheduled tasks defined in your application using cron expressions.

**Options:**

- `--interval`: Polling interval in seconds (default: 1)

### `gen-tsi` - Generate TypeScript Interfaces

```bash
jararaca gen-tsi APP_PATH FILE_PATH
```

Generates TypeScript interfaces from your Python models to ensure type safety between your frontend and backend.

### `gen-entity` - Generate Entity Template

```bash
jararaca gen-entity ENTITY_NAME FILE_PATH
```

Generates a new entity file template with proper naming conventions in different formats (snake_case, PascalCase, kebab-case).

## Quick Start

Here's a basic example of how to create a microservice with Jararaca:

```python
from app.app_config import AppConfig, AppFactoryWithAppConfig
from app.auth.auth_controller import (
    AuthConfig,
    AuthController,
    InMemoryTokenBlackListService,
    TokenBlackListService,
)
from app.extraction.models_controller import ExtractionModelController
from app.extraction.secrets_controller import SecretsController
from app.extraction.tasks_controller import TasksController
from app.providers import REDIS_TOKEN
from redis.asyncio import Redis

from jararaca import (
    AIOPikaConnectionFactory,
    AIOSQAConfig,
    AIOSqlAlchemySessionInterceptor,
    AppConfigurationInterceptor,
    HttpMicroservice,
    MessageBusPublisherInterceptor,
    Microservice,
    ProviderSpec,
    RedisWebSocketConnectionBackend,
    Token,
    WebSocketInterceptor,
    create_http_server,
)

# Create your microservice instance
app = Microservice(
    providers=[
        # Redis provider for caching and WebSocket management
        ProviderSpec(
            provide=REDIS_TOKEN,
            use_factory=AppFactoryWithAppConfig(
                lambda config: Redis.from_url(config.REDIS_URL, decode_responses=False)
            ),
            after_interceptors=True,
        ),
        # Authentication configuration
        ProviderSpec(
            provide=Token(AuthConfig, "AUTH_CONFIG"),
            use_value=AuthConfig(
                secret="your-secret-key",
                identity_refresh_token_expires_delta_seconds=60 * 60 * 24 * 30,
                identity_token_expires_delta_seconds=60 * 60,
            ),
        ),
        # Token blacklist service for JWT management
        ProviderSpec(
            provide=TokenBlackListService,
            use_value=InMemoryTokenBlackListService(),
        ),
    ],
    controllers=[
        TasksController,  # Your application controllers
    ],
    interceptors=[
        # Application configuration interceptor
        AppConfigurationInterceptor(
            global_configs=[
                (Token(AppConfig, "APP_CONFIG"), AppConfig),
            ]
        ),
        # Message bus interceptor for RabbitMQ
        AppFactoryWithAppConfig(
            lambda config: MessageBusPublisherInterceptor(
                connection_factory=AIOPikaConnectionFactory(
                    url=config.AMQP_URL,
                    exchange="jararaca_ex",
                ),
            )
        ),
        # Database session interceptor
        AppFactoryWithAppConfig(
            lambda config: AIOSqlAlchemySessionInterceptor(
                AIOSQAConfig(
                    connection_name="default",
                    url=config.DATABASE_URL,
                )
            )
        ),
        # WebSocket interceptor
        AppFactoryWithAppConfig(
            lambda config: WebSocketInterceptor(
                backend=RedisWebSocketConnectionBackend(
                    send_pubsub_channel="jararaca:websocket:send",
                    broadcast_pubsub_channel="jararaca:websocket:broadcast",
                    conn=Redis.from_url(config.REDIS_URL, decode_responses=False),
                )
            ),
        ),
    ],
)

# Create FastAPI application
http_app = create_http_server(
    HttpMicroservice(
        app=app,
        factory=fastapi_factory,
    )
)
```

## Core Concepts

### Controllers

Controllers are the heart of your microservice. They handle HTTP requests, WebSocket connections, and message bus events. Here's an example of a task controller:

```python
@MessageBusController()
@RestController("/tasks")
class TasksController:
    @Post("/")
    async def create_task(self, task: CreateTaskSchema) -> Identifiable[TaskSchema]:
        # Your implementation here
        pass

    @Get("/")
    async def get_tasks(self) -> List[TaskSchema]:
        # Your implementation here
        pass
```

### Entities

Entities represent your database models. They can be automatically mapped to and from Pydantic models:

```python
class TaskEntity(IdentifiableEntity, DatedEntity):
    __tablename__ = "tasks"

    status: Mapped[Literal["PENDING", "RUNNING", "FINISHED", "ERROR"]]
    extraction_model_id: Mapped[UUID]
    # ... other fields
```

### Query Operations

Jararaca provides powerful query operations with support for pagination and filtering:

```python
class TaskSimpleFilter(PaginatedFilter, DateOrderedFilter):
    pass

@Get("/")
async def get_tasks(self, filter: TaskSimpleFilter) -> Paginated[TaskSchema]:
    return await self.tasks_query_operations.query(filter)
```

## Advanced Features

### WebSocket Support

Real-time communication is built-in:

```python
@WebSocketEndpoint("/ws")
async def ws_endpoint(self, websocket: WebSocket):
    await websocket.accept()
    await use_ws_manager().add_websocket(websocket)
    # Handle WebSocket messages
```

### Scheduled Tasks

Run periodic tasks using cron expressions:

```python
@ScheduledAction("* * * * * */5")
async def scheduled_task(self):
    # Your scheduled task implementation
    pass
```

### Message Bus Integration

Publish and consume messages through RabbitMQ:

```python
@IncomingHandler("task")
async def process_task(self, message: Message[TaskSchema]):
    # Process incoming messages
    pass
```

## Configuration

Configure your microservice through environment variables or configuration files:

```python
class AppConfig(BaseModel):
    DATABASE_URL: str
    REDIS_URL: str
    AMQP_URL: str
```

## Best Practices

1. **Use Dependency Injection**: Leverage the DI system for better testability and maintainability
2. **Implement Proper Error Handling**: Use HTTP exceptions for API errors
3. **Use Type Hints**: Take advantage of Python's type system for better code quality
4. **Follow RESTful Principles**: Design your API endpoints following REST conventions

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
