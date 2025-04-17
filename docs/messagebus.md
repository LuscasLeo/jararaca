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
```

## Message Structure

Messages in Jararaca are built on Pydantic models, which provide type validation and serialization capabilities.

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
- `--url`: AMQP URL (default: "amqp://guest:guest@localhost/")
- `--username`: AMQP username (optional)
- `--password`: AMQP password (optional)
- `--exchange`: Exchange name (default: "jararaca_ex")
- `--queue`: Queue name (default: "jararaca_q")
- `--prefetch-count`: Number of messages to prefetch (default: 1)

## Conclusion

The Jararaca message bus system provides a powerful, type-safe way to implement asynchronous processing in your applications. With its integration with other Jararaca components, it enables building distributed systems with unified context and utilities across different runtime environments.
