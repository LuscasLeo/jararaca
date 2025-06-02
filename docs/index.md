# Jararaca Microservice Framework

Jararaca is a powerful Python microservice framework that provides a comprehensive set of tools and abstractions for building robust microservice architectures. It integrates seamlessly with FastAPI, SQLAlchemy, Redis, and RabbitMQ to deliver a complete solution for modern microservice development.

## Features

- 🚀 **FastAPI Integration**: Built-in support for FastAPI with automatic OpenAPI documentation
- 🌐 **HTTP RPC Client**: Complete REST client implementation with decorators, authentication, caching, and retry logic
- 🔌 **WebSocket Support**: Real-time communication capabilities with Redis-backed WebSocket management
- 📦 **Dependency Injection**: Flexible dependency injection system with interceptors
- 📊 **Database Integration**: SQLAlchemy integration with async support
- 📡 **Message Bus**: RabbitMQ integration for event-driven architecture
- ⚡ **Retry Mechanism**: Robust retry system with exponential backoff for resilient operations
- 🔍 **Query Operations**: Advanced query capabilities with pagination and filtering
- ⏱️ **Scheduled Tasks**: Distributed cron-based task scheduling with message broker integration

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

- `--broker-url`: The URL for the message broker (required) [env: BROKER_URL]
- `--backend-url`: The URL for the message broker backend (required) [env: BACKEND_URL]
- `--handlers`: Comma-separated list of handler names to listen to (optional) [env: HANDLERS]
- `--reload`: Enable auto-reload when Python files change (for development) [env: RELOAD]
- `--src-dir`: The source directory to watch for changes when --reload is enabled (default: "src") [env: SRC_DIR]

**Environment Variables:**
- `APP_PATH`: The application module path
- All options support environment variables as indicated above

**Example with environment variables:**
```bash
export APP_PATH="app.module:app"
export BROKER_URL="amqp://guest:guest@localhost:5672/?exchange=jararaca&prefetch_count=1"
export BACKEND_URL="redis://localhost:6379"
export HANDLERS="send_email,process_payment"
export RELOAD="true"
jararaca worker
```
- `--reload`: Enable auto-reload when Python files change (for development)
- `--src-dir`: The source directory to watch for changes when --reload is enabled (default: "src")

### `server` - HTTP Server

```bash
jararaca server APP_PATH [OPTIONS]
```

Starts a FastAPI HTTP server for your microservice.

**Options:**

- `--host`: Host to bind the server (default: "0.0.0.0") [env: HOST]
- `--port`: Port to bind the server (default: 8000) [env: PORT]

**Environment Variables:**
- `APP_PATH`: The application module path
- `HOST`: Host to bind the server
- `PORT`: Port to bind the server

**Example with environment variables:**
```bash
export APP_PATH="app.module:app"
export HOST="127.0.0.1"
export PORT="8080"
jararaca server
```

#### Alternative: Using `uvicorn` directly

For production environments, you can create an ASGI application and run it with uvicorn:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.types import Lifespan

from jararaca.presentation.http_microservice import HttpMicroservice
from jararaca.presentation.server import create_http_server


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

### `beat` - Task Scheduler

```bash
jararaca beat APP_PATH [OPTIONS]
```

Runs scheduled tasks defined in your application using cron expressions.

**Options:**

- `--interval`: Polling interval in seconds (default: 1) [env: INTERVAL]
- `--broker-url`: The URL for the message broker (required) [env: BROKER_URL]
- `--backend-url`: The URL for the message broker backend (required) [env: BACKEND_URL]
- `--actions`: Comma-separated list of action names to run (optional) [env: ACTIONS]
- `--reload`: Enable auto-reload when Python files change (for development) [env: RELOAD]
- `--src-dir`: The source directory to watch for changes when --reload is enabled (default: "src") [env: SRC_DIR]

**Environment Variables:**
- `APP_PATH`: The application module path
- All options support environment variables as indicated above

**Example with environment variables:**
```bash
export APP_PATH="app.module:app"
export INTERVAL="5"
export BROKER_URL="amqp://guest:guest@localhost:5672/?exchange=jararaca&prefetch_count=1"
export BACKEND_URL="redis://localhost:6379"
export ACTIONS="send_emails,process_payments"
export RELOAD="true"
jararaca beat
```

```bash
jararaca scheduler_v2 APP_PATH [OPTIONS]
```

Runs an enhanced version of the task scheduler with support for message broker backend integration.

**Options:**

- `--interval`: Polling interval in seconds (default: 1, required)
- `--broker-url`: The URL for the message broker (required)
- `--backend-url`: The URL for the message broker backend (required)

### `gen-tsi` - Generate TypeScript Interfaces

```bash
jararaca gen-tsi APP_PATH FILE_PATH [OPTIONS]
```

Generates TypeScript interfaces from your Python models to ensure type safety between your frontend and backend.

**Options:**

- `--watch`: Watch for file changes and regenerate TypeScript interfaces automatically [env: WATCH]
- `--src-dir`: Source directory to watch for changes (default: "src") [env: SRC_DIR]
- `--stdout`: Print generated interfaces to stdout instead of writing to a file [env: STDOUT]
- `--post-process`: Command to run after generating the interfaces, {file} will be replaced with the output file path [env: POST_PROCESS]

**Environment Variables:**
- `APP_PATH`: The application module path
- All options support environment variables as indicated above

**Example with watch mode:**

```bash
jararaca gen-tsi app.module:app interfaces.ts --watch
```

This will generate the TypeScript interfaces initially and then watch for any changes to Python files in the src directory, automatically regenerating the interfaces when changes are detected. You can stop watching with Ctrl+C.

**Example with environment variables:**
```bash
export APP_PATH="app.module:app"
export FILE_PATH="interfaces.ts"
export WATCH="true"
export SRC_DIR="src"
jararaca gen-tsi
```

**Note:** To use the watch feature, you need to install the watchdog package:

```bash
pip install jararaca[watch]
```

Or directly:

```bash
pip install watchdog
```

### `gen-entity` - Generate Entity Template

```bash
jararaca gen-entity ENTITY_NAME FILE_PATH
```

Generates a new entity file template with proper naming conventions in different formats (snake_case, PascalCase, kebab-case).

**Environment Variables:**
- `ENTITY_NAME`: The name of the entity to generate
- `FILE_PATH`: The path where the entity file should be created

**Example:**

```bash
# Using command line arguments
jararaca gen-entity User user.py

# Using environment variables
export ENTITY_NAME="User"
export FILE_PATH="user.py"
jararaca gen-entity
```

### `declare` - Declare Message Infrastructure

```bash
jararaca declare APP_PATH [OPTIONS]
```

Declares RabbitMQ infrastructure (exchanges and queues) for message handlers and schedulers without starting the actual consumption processes.

**Options:**
- `--broker-url`: Broker URL (e.g., amqp://guest:guest@localhost/) [env: BROKER_URL]
- `-i, --interactive-mode`: Enable interactive mode for queue declaration [env: INTERACTIVE_MODE]
- `-f, --force`: Force recreation by deleting existing exchanges and queues [env: FORCE]

**Environment Variables:**
- `APP_PATH`: The application module path
- `BROKER_URL`: The broker URL
- `INTERACTIVE_MODE`: Enable interactive mode
- `FORCE`: Force recreation of infrastructure

**Examples:**

```bash
# Declare infrastructure
jararaca declare myapp:app --broker-url amqp://guest:guest@localhost/

# Force recreation of queues and exchanges
jararaca declare myapp:app --broker-url amqp://guest:guest@localhost/ --force

# Using environment variables
export APP_PATH="myapp:app"
export BROKER_URL="amqp://guest:guest@localhost/"
export FORCE="true"
jararaca declare
```

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

## Messaging and Real-Time Communication

Jararaca provides powerful abstractions for both asynchronous messaging (via message bus) and real-time communication (via WebSockets).

### Message Bus Communication

The `Message` class is the foundation for all message bus communication in Jararaca. Messages can be tasks or events that flow through your microservice architecture.

```python


from pydantic import Field

from jararaca import Message


class UserCreatedMessage(Message):
    MESSAGE_TOPIC = "user.created"
    MESSAGE_TYPE = "event"  # or "task"

    user_id: str
    username: str
    email: str
```

#### Publishing Messages

You can publish messages in two ways:

1. Using the message's built-in `publish()` method:

```python
user_message = UserCreatedMessage(
    user_id="123",
    username="johndoe",
    email="john@example.com"
)
await user_message.publish()  # Uses the MESSAGE_TOPIC defined in the class
```

2. Using the publisher directly:

```python
from jararaca import use_publisher

publisher = use_publisher()
await publisher.publish(user_message, "custom.topic")  # Override the default topic
```

#### Consuming Messages

To consume messages, create a handler with the `@MessageHandler` decorator:

```python
from jararaca import Message, MessageBusController, MessageHandler

@MessageBusController()
class UserEventsController:
    @MessageHandler("user.created")
    async def handle_user_created(self, message: MessageOf[UserCreatedMessage]):
        user_data = message.payload()
        # Process the message
        print(f"User created: {user_data.username}")
```

### WebSocket Communication

The `WebSocketMessage` class enables real-time communication with connected WebSocket clients. It provides a simple way to send messages to specific rooms or broadcast to all clients.

```python
from jararaca import WebSocketMessage
from pydantic import Field

class ChatMessage(WebSocketMessage):
    MESSAGE_ID = "chat.message"

    user_id: str
    username: str
    content: str
    timestamp: str
```

#### Sending WebSocket Messages

You can send WebSocket messages to specific rooms:

```python
message = ChatMessage(
    user_id="123",
    username="johndoe",
    content="Hello, world!",
    timestamp="2025-04-17T12:00:00Z"
)

# Send to specific rooms
await message.send("room1", "room2")
```

#### Manual WebSocket Management

For more control, you can use the WebSocket manager directly:

```python
from jararaca import use_ws_manager

# Get the WebSocket manager
ws_manager = use_ws_manager()

# Add a WebSocket connection to rooms
await ws_manager.add_websocket_to_room(websocket, "room1")

# Send a message to specific rooms
await ws_manager.send(["room1", "room2"], message)

# Broadcast to all connections
await ws_manager.broadcast(message)

```

#### WebSocket Endpoint

Create a WebSocket endpoint using the `@WebSocketEndpoint` decorator:

```python
from jararaca import WebSocketEndpoint, RestController
from fastapi import WebSocket

@RestController("/ws")
@RegisterWebSocketMessage(ChatMessage) # Register the WebSocket message in order to be generated in the ts files
class WebSocketController:
    @WebSocketEndpoint("/chat/{room_id}")
    async def chat_endpoint(self, websocket: WebSocket, room_id: str):
        await websocket.accept()

        # Add to room
        ws_manager = use_ws_manager()
        await ws_manager.add_websocket_to_room(websocket, room_id)

        try:
            while True:
                data = await websocket.receive_text()
                message = ChatMessage(
                    user_id="123",
                    username="johndoe",
                    content=data,
                    timestamp="2025-04-17T12:00:00Z"
                )
                await message.send(room_id)
        except:
            # Remove from room when connection closes
            await ws_manager.remove_websocket_from_room(websocket, room_id)
```

### Integration Between Message Bus and WebSockets

One of Jararaca's strengths is the ability to seamlessly integrate message bus events with WebSocket communication, enabling real-time updates from background processes:

```python
@MessageBusController()
@RestController("/notifications")
class NotificationController:
    @MessageHandler("user.activity")
    async def handle_user_activity(self, message: MessageOf[UserActivityMessage]):
        user_data = message.payload()

        # Create a WebSocket message
        notification = ActivityNotification(
            user_id=user_data.user_id,
            action=user_data.action,
            timestamp=user_data.timestamp
        )

        # Send to user's room
        await notification.send(f"user-{user_data.user_id}")
```

This allows you to build truly reactive systems where events processed in background workers can immediately update connected clients through WebSockets.

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


@MessageHandler(TaskSchema)
async def process_task(self, message: MessageOf[TaskSchema]):
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
