# Jararaca Scheduler System

The scheduler system in Jararaca provides robust task scheduling capabilities that allow you to run periodic tasks using cron expressions. This document explains how the scheduler works, its different implementations, and how to use it in your applications.

## Overview

The Jararaca scheduler allows you to:

- Run background tasks at scheduled intervals
- Use cron expressions for flexible scheduling
- Control overlap behavior (whether to allow multiple instances of the same task)
- Distribute scheduled tasks across multiple instances
- Handle delayed message execution

The scheduler is implemented through the BeatWorker which provides distributed task scheduling via a message broker:

```mermaid
graph TD
    A[Microservice] --> B[BeatWorker]
    B --> C[Message Broker]
    B --> D[Backend Store]
    C --> E[Message Processing]
    D --> F[Last Execution Time]
    D --> G[Delayed Messages]
```

## Using the Scheduler

### Defining Scheduled Tasks

You can define scheduled tasks using the `@ScheduledAction` decorator:

```python
from jararaca import ScheduledAction


class TasksController:
    @ScheduledAction("*/5 * * * *")  # Run every 5 minutes
    async def scheduled_task(self):
        # Your task implementation
        print("This runs every 5 minutes")

    @ScheduledAction("0 */2 * * *", allow_overlap=False, timeout=60)
    async def heavy_task(self):
        # A heavier task that shouldn't overlap
        print("This runs every 2 hours without overlap")
```

### Scheduler Decorator Options

The `@ScheduledAction` decorator accepts several parameters:

- `cron`: A string representing the cron expression for the scheduled action
- `allow_overlap`: A boolean indicating if new executions should start even if the previous one is still running (default: `False`)
- `exclusive`: A boolean indicating if the scheduled action should be executed in only one instance of the application (requires a distributed backend, default: `True`)
- `timeout`: An integer representing the timeout for the scheduled action in seconds (default: `None`)
- `exception_handler`: A callable that will be called when an exception is raised during execution (default: `None`)

### Cron Expressions

Jararaca uses standard cron expressions for scheduling. Here are some examples:

- `* * * * *` - Run every minute
- `*/15 * * * *` - Run every 15 minutes
- `0 * * * *` - Run at the beginning of every hour
- `0 0 * * *` - Run at midnight every day
- `0 0 * * 0` - Run at midnight every Sunday
- `0 0 1 * *` - Run at midnight on the first day of every month

## Using the BeatWorker Scheduler

The BeatWorker scheduler provides distributed task execution through a message broker:

```python
from jararaca import Microservice, ScheduledAction
from jararaca.scheduler.beat_worker import BeatWorker

app = Microservice(
    # Your microservice configuration
)

# Run the scheduler
beat_worker = BeatWorker(
    app=app,
    interval=1,
    backend_url="redis://localhost:6379",
    broker_url="amqp://guest:guest@localhost:5672/?exchange=jararaca",
    scheduled_action_names=None  # Optional set of action names to run
)
beat_worker.run()
```

You can also use the CLI command to run the scheduler:

```bash
# Standard beat scheduler execution
jararaca beat app_module:app --interval 1 --broker-url "amqp://guest:guest@localhost:5672/?exchange=jararaca" --backend-url "redis://localhost:6379"

# With auto-reload for development (automatically restarts when Python files change)
jararaca beat app_module:app --interval 1 --broker-url "amqp://guest:guest@localhost:5672/?exchange=jararaca" --backend-url "redis://localhost:6379" --reload

# Using environment variables
export APP_PATH="app_module:app"
export INTERVAL="1"
export BROKER_URL="amqp://guest:guest@localhost:5672/?exchange=jararaca"
export BACKEND_URL="redis://localhost:6379"
export RELOAD="true"
export SRC_DIR="src"
jararaca beat
```

All command options support environment variables:
- `APP_PATH`: The application module path [required]
- `INTERVAL`: Polling interval in seconds [default: 1]
- `BROKER_URL`: The URL for the message broker [required]
- `BACKEND_URL`: The URL for the message broker backend [required]
- `ACTIONS`: Comma-separated list of action names to run [optional]
- `RELOAD`: Enable auto-reload when Python files change [optional]
- `SRC_DIR`: The source directory to watch for changes when using reload [default: "src"]
```

app = Microservice(
    # Your microservice configuration
)

# Run the enhanced scheduler
scheduler = SchedulerV2(
    app=app,
    interval=1,
    broker_url="amqp://guest:guest@localhost:5672/?exchange=jararaca_ex",
    backend_url="redis://localhost:6379",
)
scheduler.run()
```

### Message Broker Integration

The V2 scheduler uses a message broker (currently supporting RabbitMQ) to distribute tasks:

1. The scheduler determines when a task should run based on its cron expression
2. Instead of executing the task directly, it sends a message to the message broker
3. A worker picks up the message and executes the task
4. The backend store (Redis) tracks execution state to prevent overlap when configured

This architecture allows for better scalability and reliability:

```mermaid
sequenceDiagram
    participant S as SchedulerV2
    participant B as Message Broker
    participant R as Redis Backend
    participant W as Worker

    S->>R: Check last execution time
    R-->>S: Return last execution time
    S->>S: Determine if task should run
    S->>B: Publish task message
    S->>R: Update last execution time
    B-->>W: Deliver task message
    W->>R: Mark task as running
    W->>W: Execute task
    W->>R: Mark task as completed
```

### Delayed Message Queue

The V2 scheduler also supports delayed messages:

```python
from jararaca import use_publisher
from jararaca.scheduler.types import DelayedMessageData


# Schedule a message to be published at a future time
async def schedule_reminder():
    message = ReminderMessage(
        user_id="123",
        message="Don't forget your appointment!"
    )

    # Current time + 1 hour in seconds
    dispatch_time = int(time.time()) + 3600

    # Get publisher
    publisher = use_publisher()

    # Schedule delayed message
    await publisher.publish_delayed(
        message,
        dispatch_time=dispatch_time
    )
```

## Redis Backend Implementation

The Redis backend implementation provides:

1. **Distributed Locking** - Ensures tasks only run on one instance when exclusivity is required
2. **Execution Tracking** - Tracks the running state of tasks to prevent overlap
3. **Delayed Message Queue** - Manages messages scheduled for future delivery

The implementation uses Redis data structures:
- Keys for last execution time and dispatch time
- Sorted sets for delayed message queue
- Hash sets for execution indicators

## Running the Scheduler

### CLI Command for Basic Scheduler

```bash
jararaca scheduler APP_PATH [OPTIONS]
```

**Options:**
- `--interval`: Polling interval in seconds (default: 1)

### CLI Command for Enhanced Scheduler (V2)

```bash
jararaca scheduler_v2 APP_PATH [OPTIONS]
```

**Options:**
- `--interval`: Polling interval in seconds (default: 1, required)
- `--broker-url`: The URL for the message broker (required)
- `--backend-url`: The URL for the message broker backend (required)

## Best Practices

1. **Task Duration** - Be mindful of task duration, especially for frequent tasks
2. **Error Handling** - Implement proper error handling in your tasks
3. **Overlap Control** - Use `allow_overlap=False` for resource-intensive tasks
4. **Timeouts** - Set appropriate timeouts to prevent stuck tasks
5. **Monitoring** - Log task execution for monitoring purposes

## Conclusion

The Jararaca scheduler system provides a powerful, flexible way to implement periodic tasks in your applications. With two implementations to choose from, you can select the one that best fits your application's requirements, from simple local scheduling to complex distributed task execution.
