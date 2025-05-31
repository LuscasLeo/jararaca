# Jararaca Message Bus Architecture

The message bus system in Jararaca provides a robust infrastructure for asynchronous message processing using a publisher-consumer pattern. This document explains how the message bus works, from message definition to processing flows.

## Overview

The message bus system consists of several key components that work together to provide a seamless experience for defining, publishing, and consuming messages.

```mermaid
graph TB
    subgraph "Message Definition"
        Message[Message]
        MessageOf[MessageOf]
    end

    subgraph "Message Handling"
        MessageHandler[MessageHandler]
        MessageBusController[MessageBusController]
        BusMessageController[BusMessageController]
    end

    subgraph "Worker Infrastructure"
        MessageBusWorker[MessageBusWorker]
        AioPikaMicroserviceConsumer[AioPikaMicroserviceConsumer]
        MessageHandlerCallback[MessageHandlerCallback]
    end

    subgraph "Utilities"
        ack[ack]
        nack[nack]
        retry[retry]
        retry_later[retry_later]
        reject[reject]
    end

    Message --> MessageOf
    MessageHandler --> MessageBusController
    MessageBusController --> MessageBusWorker
    MessageBusWorker --> AioPikaMicroserviceConsumer
    AioPikaMicroserviceConsumer --> MessageHandlerCallback
    MessageHandlerCallback --> BusMessageController
    BusMessageController --> ack
    BusMessageController --> nack
    BusMessageController --> reject
    BusMessageController --> retry
    BusMessageController --> retry_later
```

## Message Structure

Messages in Jararaca are built on Pydantic models, which provide type validation and serialization capabilities.

### Message Types: Tasks vs Events

Jararaca supports two fundamental message types:

- **Tasks**: Designed to be handled exactly once by a single MessageHandler. It is not recommended to have multiple MessageHandlers listening to the same Task type. Tasks represent commands or operations that should be executed once.

- **Events**: Can be listened to by multiple parts of the application. Events are ideal for scenarios where different components need to react to the same occurrence, providing looser coupling throughout the codebase.

```mermaid
graph LR
    Task[Task Message] --> Handler1[Single Handler]
    Event[Event Message] --> HandlerA[Handler A]
    Event --> HandlerB[Handler B]
    Event --> HandlerC[Handler C]
```

When designing your message architecture, consider:

- Use Tasks when an operation should be performed exactly once
- Use Events when multiple systems need to react to the same occurrence
- Events promote better decoupling between components

### Base Message Class

```mermaid
classDiagram
    class Message {
        +MESSAGE_TOPIC: ClassVar[str]
        +MESSAGE_TYPE: ClassVar[Literal["task", "event"]]
        +publish() async
    }
    class BaseModel {
        +model_validate_json()
        +json()
    }
    class UserCreatedMessage {
        +MESSAGE_TOPIC: "user.created"
        +MESSAGE_TYPE: "event"
        +user_id: str
        +username: str
        +email: str
    }
    BaseModel <|-- Message
    Message <|-- UserCreatedMessage
```

### Example Message Definition

```python
from jararaca import Message


class UserCreatedMessage(Message):
    MESSAGE_TOPIC = "user.created"
    MESSAGE_TYPE = "event"  # or "task"

    user_id: str
    username: str
    email: str
```

## Message Processing Flow

When a message is published, it goes through several processing stages before being handled by the appropriate consumer.

```mermaid
sequenceDiagram
    participant P as Publisher
    participant E as Exchange (RabbitMQ)
    participant Q as Queue
    participant W as MessageBusWorker
    participant C as Consumer (Handler)

    P->>E: publish(message, topic)
    E->>Q: route message by topic
    W->>Q: consume messages
    Q->>W: deliver message
    W->>C: invoke message handler
    C->>W: process & acknowledge
```

## Message Routing: Exchanges and Queues

Jararaca's message bus system leverages RabbitMQ's exchange and queue architecture to efficiently route messages between publishers and consumers.

### Exchange and Queue Structure Example

The following diagram illustrates how the exchange and queue system works in a typical Jararaca application:

```mermaid
graph LR
    subgraph Publishers
        P1[UserService]
        P2[OrderService]
        P3[NotificationService]
    end

    subgraph "Exchange (jararaca_ex)"
        E[Topic Exchange]
    end

    subgraph Queues
        Q1[user_service_queue]
        Q2[order_service_queue]
        Q3[notification_service_queue]
        Q4[analytics_service_queue]
    end

    subgraph Consumers
        C1[UserServiceHandler]
        C2[OrderServiceHandler]
        C3[NotificationServiceHandler]
        C4[AnalyticsServiceHandler]
    end

    P1 -->|user.created| E
    P1 -->|user.updated| E
    P2 -->|order.created| E
    P2 -->|order.updated| E
    P3 -->|notification.sent| E

    E -->|user.created| Q1
    E -->|user.updated| Q1
    E -->|order.created| Q2
    E -->|order.updated| Q2
    E -->|notification.sent| Q3

    %% Events can be routed to multiple queues
    E -->|user.created| Q4
    E -->|order.created| Q4

    Q1 --> C1
    Q2 --> C2
    Q3 --> C3
    Q4 --> C4

    style E fill:#f96,stroke:#333,stroke-width:2px
    style Q1 fill:#9cf,stroke:#333,stroke-width:1px
    style Q2 fill:#9cf,stroke:#333,stroke-width:1px
    style Q3 fill:#9cf,stroke:#333,stroke-width:1px
    style Q4 fill:#9cf,stroke:#333,stroke-width:1px
```

### How Message Routing Works

1. **Publishers** send messages to the exchange with a specific topic (e.g., `user.created`, `order.updated`).
2. The **Exchange** routes these messages to queues based on binding patterns.
3. **Queues** hold messages until they are consumed.
4. **Consumers** process messages from their assigned queues.

#### Key Concepts:

- **Topic-based routing**: Messages are routed based on their topic (e.g., `user.created`)
- **Multiple bindings**: A single exchange can route to multiple queues (especially useful for event messages)
- **Service isolation**: Each service typically has its own queue
- **Message persistence**: Messages remain in queues until processed, even if consumers are temporarily unavailable

#### Example Queue Binding Configuration

```python
from jararaca import MessageBusController, MessageHandler
from jararaca.messagebus.worker import AioPikaWorkerConfig

# Define worker configuration with queue binding patterns
worker_config = AioPikaWorkerConfig(
    url="amqp://guest:guest@localhost/",
    exchange="jararaca_ex",
    queue="user_service_queue",
    binding_keys=["user.*", "notification.user.*"]  # This queue receives all user-related topics
)
```

This message routing architecture allows for flexible and scalable communication patterns between different parts of your application, supporting both direct task assignment and broad event publishing.

## Worker Infrastructure

The MessageBusWorker is the central piece that orchestrates message consumption and processing.

```mermaid
classDiagram
    class MessageBusWorker {
        -app: Microservice
        -config: AioPikaWorkerConfig
        -container: Container
        -lifecycle: AppLifecycle
        -uow_context_provider: UnitOfWorkContextProvider
        -_consumer: AioPikaMicroserviceConsumer
        +start_async()
        +start_sync()
    }

    class AioPikaMicroserviceConsumer {
        -config: AioPikaWorkerConfig
        -message_handler_set: Set[MessageHandlerData]
        -incoming_map: Dict[str, MessageHandlerData]
        -uow_context_provider: UnitOfWorkContextProvider
        -shutdown_event: Event
        -lock: Lock
        -tasks: Set[Task]
        +consume()
        +wait_all_tasks_done()
    }

    class MessageHandlerCallback {
        -consumer: AioPikaMicroserviceConsumer
        -queue_name: str
        -routing_key: str
        -message_handler: MessageHandlerData
        +message_consumer()
        +handle_message_consume_done()
        +handle_reject_message()
        +handle_message()
    }

    MessageBusWorker --> AioPikaMicroserviceConsumer
    AioPikaMicroserviceConsumer --> MessageHandlerCallback
```

### Worker Initialization Process

```mermaid
flowchart TD
    A[MessageBusWorker Initialization] --> B[Load App Controllers]
    B --> C[Find MessageBusControllers]
    C --> D[Extract Message Handlers]
    D --> E[Create AioPikaMicroserviceConsumer]
    E --> F[Connect to RabbitMQ]
    F --> G[Declare Exchanges & Queues]
    G --> H[Bind Queues to Topics]
    H --> I[Start Consuming Messages]
```

### Message Consumption Process

```mermaid
flowchart TD
    A[Message Received] --> B[Create Task for Message]
    B --> C[Extract Message Type]
    C --> D[Deserialize Message]
    D --> E[Create MessageBusAppContext]
    E --> F[Setup BusMessageController]
    F --> G[Invoke Handler Function]
    G -->|Success| H[Acknowledge Message]
    G -->|Failure| I[Handle Exception]
    I -->|Retry| J[Requeue Message]
    I -->|Discard| K[Reject Message]
```

## Handler Registration

Jararaca uses a declarative approach to register message handlers through decorators.

```mermaid
classDiagram
    class MessageBusController {
        -messagebus_factory: Callable
        +get_messagebus_factory()
        +register()
        +get_messagebus()
    }

    class MessageHandler {
        -message_type: Type[Message]
        -timeout: Optional[int]
        -exception_handler: Optional[Callable]
        -requeue_on_exception: bool
        -auto_ack: bool
        +register()
        +get_message_incoming()
    }

    class MessageHandlerData {
        +message_type: Type[Message]
        +spec: MessageHandler
        +callable: Callable
    }

    MessageBusController --> MessageHandler
    MessageHandler --> MessageHandlerData
```

### Example Handler Definition

```python
from jararaca import Message, MessageBusController, MessageHandler, MessageOf


@MessageBusController()
class UserEventsController:
    @MessageHandler(UserCreatedMessage, timeout=30, nack_on_exception=True)
    async def handle_user_created(self, message: MessageOf[UserCreatedMessage]):
        user_data = message.payload()
        # Process the message
        print(f"User created: {user_data.username}")
```

## Message Control Flow

During message processing, handlers can control the message acknowledgment flow.

```mermaid
sequenceDiagram
    participant H as Handler
    participant C as BusMessageController
    participant Q as Queue

    H->>C: ack()
    C->>Q: Acknowledge Message

    H->>C: nack()
    C->>Q: Negative Acknowledge

    H->>C: reject()
    C->>Q: Reject Message

    H->>C: retry()
    C->>Q: Requeue Message
```

### Message Control Utilities

```python
from jararaca import ack, nack, reject, retry


@MessageBusController()
class TaskProcessor:
    @MessageHandler(TaskMessage, auto_ack=False)
    async def process_task(self, message: MessageOf[TaskMessage]):
        try:
            task_data = message.payload()
            # Process the task
            await self.process_task_data(task_data)
            # Manually acknowledge successful processing
            await ack()
        except TemporaryError:
            # Request message retry
            await retry()
        except PermanentError:
            # Reject the message
            await reject()
```

## Error Handling

The message bus provides comprehensive error handling mechanisms:

```mermaid
flowchart TD
    A[Message Processing] -->|Success| B[Acknowledge]
    A -->|Failure| C[Exception Handling]
    C -->|Has Exception Handler| D[Custom Handler]
    C -->|No Exception Handler| E[Default Logging]
    D -->|Requeue Configured| F[Retry Message]
    D -->|Discard Configured| G[Reject Message]
    E -->|Requeue Configured| F
    E -->|Discard Configured| G
```

## Integration with Other Jararaca Components

The message bus system integrates with other Jararaca components for a unified experience:

```mermaid
flowchart LR
    A[Message Bus] <--> B[Database Sessions]
    A <--> C[WebSockets]
    A <--> D[Scheduler]
    A <--> E[HTTP API]

    B <-- use_session --> F[Shared Context]
    C <-- use_ws_manager --> F
    A <-- use_publisher --> F
```

### WebSocket Integration Example

```python
@MessageBusController()
class NotificationController:
    @MessageHandler(UserActivityMessage)
    async def handle_user_activity(self, message: MessageOf[UserActivityMessage]):
        user_data = message.payload()

        # Create a WebSocket message
        notification = ActivityNotification(
            user_id=user_data.user_id,
            action=user_data.action,
            timestamp=user_data.timestamp
        )

        # Send to user's room using WebSocket
        await notification.send(f"user-{user_data.user_id}")
```

## Command Line Usage

You can start a message bus worker using the Jararaca CLI:

```bash
jararaca worker APP_PATH [OPTIONS]
```

Options:

- `--broker-url`: The URL for the message broker (required) [env: BROKER_URL]
- `--backend-url`: The URL for the message broker backend (required) [env: BACKEND_URL]
- `--handlers`: Comma-separated list of handler names to listen to (optional) [env: HANDLERS]
- `--reload`: Enable auto-reload when Python files change (for development) [env: RELOAD]
- `--src-dir`: The source directory to watch for changes when --reload is enabled (default: "src") [env: SRC_DIR]

Examples:

```bash
# Standard worker execution
jararaca worker myapp.main:app --broker-url "amqp://guest:guest@localhost:5672/?exchange=jararaca" --backend-url "redis://localhost:6379"

# With auto-reload for development
jararaca worker myapp.main:app --broker-url "amqp://guest:guest@localhost:5672/?exchange=jararaca" --backend-url "redis://localhost:6379" --reload

# Using environment variables
export APP_PATH="myapp.main:app"
export BROKER_URL="amqp://guest:guest@localhost:5672/?exchange=jararaca"
export BACKEND_URL="redis://localhost:6379"
export RELOAD="true"
export SRC_DIR="src"
export RELOAD="true"
jararaca worker
```

## Conclusion

The Jararaca message bus system provides a powerful, type-safe way to implement asynchronous processing in your applications. With its integration with other Jararaca components, it enables building distributed systems with unified context and utilities across different runtime environments.
