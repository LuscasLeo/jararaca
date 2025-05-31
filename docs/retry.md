# Retry Mechanism with Exponential Backoff

Jararaca implements a robust retry mechanism with exponential backoff for handling transient failures in RabbitMQ connections and operations. This mechanism helps the system gracefully handle temporary network issues, broker unavailability, and other transient failures.

## Core Components

The retry system consists of these main components:

1. `RetryConfig` - Configuration class for customizing retry behavior
2. `retry_with_backoff` - Utility function to execute operations with retry
3. `with_retry` - Decorator for applying retry logic to functions

## Retry Configuration

The `RetryConfig` class allows customization of various retry parameters:

```python
class RetryConfig:
    def __init__(
        self,
        max_retries: int = 5,         # Maximum number of retry attempts
        initial_delay: float = 1.0,    # Initial delay between retries (seconds)
        max_delay: float = 60.0,       # Maximum delay between retries (seconds)
        backoff_factor: float = 2.0,   # Multiplier for delay after each retry
        jitter: bool = True,           # Add randomness to delay to prevent thundering herd
    ):
        ...
```

## Integration with MessageBus Worker

The RabbitMQ consumer in the message bus system uses the retry mechanism in several key areas:

1. **Connection Establishment**: When establishing a connection to RabbitMQ, the system will automatically retry with increasing backoff periods if the connection fails.

2. **Channel Creation**: When creating channels for publishing or consuming messages, failures trigger the retry mechanism.

3. **Consumer Setup**: Setting up message consumers uses retry logic to handle temporary failures.

## URL Configuration Parameters

Retry behavior can be customized through URL parameters when configuring the RabbitMQ connection:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `connection_retry_max` | Maximum number of connection retry attempts | 5 |
| `connection_retry_delay` | Initial delay between connection retries (seconds) | 1.0 |
| `connection_retry_max_delay` | Maximum delay between connection retries (seconds) | 60.0 |
| `connection_retry_backoff` | Multiplier for delay after each connection retry | 2.0 |
| `consumer_retry_max` | Maximum number of consumer setup retry attempts | 3 |
| `consumer_retry_delay` | Initial delay between consumer setup retries (seconds) | 0.5 |
| `consumer_retry_max_delay` | Maximum delay between consumer setup retries (seconds) | 5.0 |
| `consumer_retry_backoff` | Multiplier for delay after each consumer setup retry | 2.0 |

## Example Usage

```python
# Configure with custom retry settings in URL:
broker_url = "amqp://guest:guest@localhost:5672/?exchange=jararaca&prefetch_count=10&connection_retry_max=10&connection_retry_delay=2.0"

# Use custom retry configuration in code:
from jararaca.utils.retry import RetryConfig, retry_with_backoff

config = RetryConfig(max_retries=3, initial_delay=1.0, max_delay=30.0)

async def connect_with_retry():
    return await retry_with_backoff(
        establish_connection,
        retry_config=config,
        retry_exceptions=(ConnectionError, TimeoutError)
    )
```

## Benefits

1. **Resilience** - The system can recover automatically from transient failures
2. **Reduced downtime** - Automatic reconnection minimizes service disruption
3. **Configuration flexibility** - Retry behavior can be tailored to different environments
4. **Smart backoff** - Exponential backoff with jitter prevents overloading services during recovery
