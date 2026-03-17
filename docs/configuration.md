# Configuration Constants

The Jararaca framework provides environment variable-based configuration for internal behavior. These settings are defined in the `jararaca.const` module and control defaults throughout the framework.

## Overview

Configuration is managed through environment variables that:

- Control framework behavior without code changes
- Set defaults for pagination, retries, connection pools, and more
- Allow environment-specific tuning (development vs. production)
- Are evaluated once at import time

**Important**: The const module is for internal framework use. As a library user, you configure the framework via environment variables and extend the provided classes (like `PaginatedFilter`) rather than using constants directly.

## Usage

### Basic Usage (Library Users)

Extend framework classes which automatically respect the configured constants:

```python
from jararaca import PaginatedFilter


# PaginatedFilter automatically uses configured values
class MyFilter(PaginatedFilter):
    # Add your custom filters here
    search: str | None = None
    category: str | None = None

# page_size respects JARARACA_PAGINATION_MAX_PAGE_SIZE
# defaults to JARARACA_PAGINATION_DEFAULT_PAGE_SIZE
```

### Configuration Categories

#### Pagination

Control default pagination behavior:

```python
const.PAGINATION_DEFAULT_PAGE_SIZE  # Default: 10
const.PAGINATION_MAX_PAGE_SIZE      # Default: 100
const.PAGINATION_DEFAULT_PAGE       # Default: 0
```

**Environment Variables:**
- `JARARACA_PAGINATION_DEFAULT_PAGE_SIZE`
- `JARARACA_PAGINATION_MAX_PAGE_SIZE`
- `JARARACA_PAGINATION_DEFAULT_PAGE`

#### Database

SQLAlchemy connection pool settings:

```python
const.DB_POOL_SIZE          # Default: 5
const.DB_MAX_OVERFLOW       # Default: 10
const.DB_POOL_TIMEOUT       # Default: 30.0
const.DB_POOL_RECYCLE       # Default: 3600
```

**Environment Variables:**
- `JARARACA_DB_POOL_SIZE`
- `JARARACA_DB_MAX_OVERFLOW`
- `JARARACA_DB_POOL_TIMEOUT`
- `JARARACA_DB_POOL_RECYCLE`

#### HTTP RPC Client

Default HTTP client configuration:

```python
const.HTTP_RPC_DEFAULT_TIMEOUT          # Default: 30.0
const.HTTP_RPC_DEFAULT_RETRY_ATTEMPTS   # Default: 3
const.HTTP_RPC_DEFAULT_RETRY_BACKOFF    # Default: 1.0
```

**Environment Variables:**
- `JARARACA_HTTP_RPC_DEFAULT_TIMEOUT`
- `JARARACA_HTTP_RPC_DEFAULT_RETRY_ATTEMPTS`
- `JARARACA_HTTP_RPC_DEFAULT_RETRY_BACKOFF`

#### Retry Policies

Generic retry configuration defaults:

```python
const.RETRY_DEFAULT_MAX_RETRIES     # Default: 5
const.RETRY_DEFAULT_INITIAL_DELAY   # Default: 1.0
const.RETRY_DEFAULT_MAX_DELAY       # Default: 60.0
const.RETRY_DEFAULT_BACKOFF_FACTOR  # Default: 2.0
const.RETRY_DEFAULT_JITTER          # Default: True
```

**Environment Variables:**
- `JARARACA_RETRY_DEFAULT_MAX_RETRIES`
- `JARARACA_RETRY_DEFAULT_INITIAL_DELAY`
- `JARARACA_RETRY_DEFAULT_MAX_DELAY`
- `JARARACA_RETRY_DEFAULT_BACKOFF_FACTOR`

#### Message Bus Worker

Message bus connection and consumer settings:

```python
const.MESSAGEBUS_DEFAULT_PREFETCH_COUNT             # Default: 10
const.MESSAGEBUS_CONNECTION_RETRY_MAX               # Default: 15
const.MESSAGEBUS_CONNECTION_RETRY_INITIAL_DELAY     # Default: 1.0
const.MESSAGEBUS_CONNECTION_RETRY_MAX_DELAY         # Default: 60.0
const.MESSAGEBUS_CONNECTION_RETRY_BACKOFF           # Default: 2.0
const.MESSAGEBUS_CONSUMER_RETRY_MAX                 # Default: 15
const.MESSAGEBUS_CONSUMER_RETRY_INITIAL_DELAY       # Default: 0.5
const.MESSAGEBUS_CONSUMER_RETRY_MAX_DELAY           # Default: 40.0
const.MESSAGEBUS_CONSUMER_RETRY_BACKOFF             # Default: 2.0
const.MESSAGEBUS_CONNECTION_HEARTBEAT_INTERVAL      # Default: 30.0
const.MESSAGEBUS_CONNECTION_HEALTH_CHECK_INTERVAL   # Default: 10.0
```

**Environment Variables:**
- `JARARACA_MESSAGEBUS_DEFAULT_PREFETCH_COUNT`
- `JARARACA_MESSAGEBUS_CONNECTION_RETRY_MAX`
- `JARARACA_MESSAGEBUS_CONNECTION_RETRY_INITIAL_DELAY`
- `JARARACA_MESSAGEBUS_CONNECTION_RETRY_MAX_DELAY`
- `JARARACA_MESSAGEBUS_CONNECTION_RETRY_BACKOFF`
- `JARARACA_MESSAGEBUS_CONSUMER_RETRY_MAX`
- `JARARACA_MESSAGEBUS_CONSUMER_RETRY_INITIAL_DELAY`
- `JARARACA_MESSAGEBUS_CONSUMER_RETRY_MAX_DELAY`
- `JARARACA_MESSAGEBUS_CONSUMER_RETRY_BACKOFF`
- `JARARACA_MESSAGEBUS_CONNECTION_HEARTBEAT_INTERVAL`
- `JARARACA_MESSAGEBUS_CONNECTION_HEALTH_CHECK_INTERVAL`

#### Scheduler

Scheduler configuration:

```python
const.SCHEDULER_MAX_CONCURRENT_JOBS                 # Default: 10
const.SCHEDULER_CONNECTION_RETRY_MAX                # Default: 10
const.SCHEDULER_CONNECTION_RETRY_INITIAL_DELAY      # Default: 2.0
const.SCHEDULER_CONNECTION_RETRY_MAX_DELAY          # Default: 60.0
const.SCHEDULER_CONNECTION_RETRY_BACKOFF            # Default: 2.0
const.SCHEDULER_DISPATCH_RETRY_MAX                  # Default: 3
const.SCHEDULER_DISPATCH_RETRY_INITIAL_DELAY        # Default: 1.0
const.SCHEDULER_DISPATCH_RETRY_MAX_DELAY            # Default: 10.0
const.SCHEDULER_DISPATCH_RETRY_BACKOFF              # Default: 2.0
const.SCHEDULER_CONNECTION_HEARTBEAT_INTERVAL       # Default: 30.0
const.SCHEDULER_HEALTH_CHECK_INTERVAL               # Default: 15.0
const.SCHEDULER_CONNECTION_WAIT_TIMEOUT             # Default: 300.0
const.SCHEDULER_MAX_POOL_SIZE                       # Default: 10
const.SCHEDULER_POOL_RECYCLE_TIME                   # Default: 3600.0
```

**Environment Variables:**
- `JARARACA_SCHEDULER_MAX_CONCURRENT_JOBS`
- `JARARACA_SCHEDULER_CONNECTION_RETRY_MAX`
- `JARARACA_SCHEDULER_CONNECTION_RETRY_INITIAL_DELAY`
- `JARARACA_SCHEDULER_CONNECTION_RETRY_MAX_DELAY`
- `JARARACA_SCHEDULER_CONNECTION_RETRY_BACKOFF`
- And more (see const module for full list)

#### WebSocket

WebSocket connection settings:

```python
const.WEBSOCKET_CONNECTION_TIMEOUT  # Default: 60.0
const.WEBSOCKET_PING_INTERVAL       # Default: 30.0
const.WEBSOCKET_MAX_MESSAGE_SIZE    # Default: 1048576 (1MB)
```

**Environment Variables:**
- `JARARACA_WEBSOCKET_CONNECTION_TIMEOUT`
- `JARARACA_WEBSOCKET_PING_INTERVAL`
- `JARARACA_WEBSOCKET_MAX_MESSAGE_SIZE`

#### Observability

Tracing configuration:

```python
const.OBSERVABILITY_TRACE_SAMPLE_RATE  # Default: 1.0
```

**Environment Variables:**
- `JARARACA_OBSERVABILITY_TRACE_SAMPLE_RATE`

#### General

Application-wide settings:

```python
const.DEFAULT_TIMEZONE  # Default: "UTC"
const.APP_ENVIRONMENT   # Default: None
const.DEBUG             # Default: False
```

**Environment Variables:**
- `JARARACA_DEFAULT_TIMEZONE` - IANA timezone name (e.g., "UTC", "America/New_York", "Europe/Paris")
- `JARARACA_APP_ENVIRONMENT` - Application environment string (e.g., "development", "production")
- `JARARACA_DEBUG` - Enable debug mode (truthy values: "1", "true", "yes", "on")

**Note**: `DEFAULT_TIMEZONE` is used by the beat scheduler for cron job verification to ensure scheduled actions respect the configured timezone.

## Examples

### Example 1: Extending PaginatedFilter

```python
from jararaca import PaginatedFilter


# Automatically respects JARARACA_PAGINATION_* environment variables
class ProductFilter(PaginatedFilter):
    search: str | None = None
    category: str | None = None
    min_price: float | None = None
    max_price: float | None = None

# Usage in controller
@Get("/products")
async def list_products(self, filter: ProductFilter) -> list[Product]:
    # page_size is limited by JARARACA_PAGINATION_MAX_PAGE_SIZE
    # defaults to JARARACA_PAGINATION_DEFAULT_PAGE_SIZE
    return await self.product_service.list(filter)
```

### Example 2: Message Handler with Configured Timeouts

```python
from jararaca import MessageBusController, MessageHandler, MessageOf


@MessageBusController()
class OrderController:
    # Timeout respects JARARACA_MESSAGEBUS_HANDLER_TIMEOUT if not specified
    @MessageHandler(OrderCreatedEvent)
    async def handle_order_created(self, message: MessageOf[OrderCreatedEvent]) -> None:
        # Handler logic
        pass
```

### Example 3: HTTP RPC Client with Configured Defaults

```python
from jararaca.rpc.http import (
    Get,
    HttpRpcClientBuilder,
    HTTPXHttpRPCAsyncBackend,
    RestClient,
)


@RestClient
class ProductAPI:
    @Get("/products/{product_id}")
    async def get_product(self, product_id: str) -> Product:
        # Timeout and retry defaults from JARARACA_HTTP_RPC_* variables
        pass

# Build client - backend uses JARARACA_HTTP_RPC_DEFAULT_TIMEOUT
client = HttpRpcClientBuilder(
    HTTPXHttpRPCAsyncBackend(prefix_url="https://api.example.com")
).build(ProductAPI)
```

## Setting Environment Variables

### Development (.env file)

```bash
# Pagination
JARARACA_PAGINATION_DEFAULT_PAGE_SIZE=25
JARARACA_PAGINATION_MAX_PAGE_SIZE=200

# Database
JARARACA_DB_POOL_SIZE=15
JARARACA_DB_MAX_OVERFLOW=25

# HTTP RPC
JARARACA_HTTP_RPC_DEFAULT_TIMEOUT=45.0

# Retry
JARARACA_RETRY_DEFAULT_MAX_RETRIES=10

# Message Bus
JARARACA_MESSAGEBUS_DEFAULT_PREFETCH_COUNT=20

# General
JARARACA_DEFAULT_TIMEZONE=America/New_York
```

### Production (Docker/Kubernetes)

```bash
docker run \
  -e JARARACA_PAGINATION_MAX_PAGE_SIZE=500 \
  -e Extend framework classes**: Use `PaginatedFilter` and other framework classes that automatically respect configuration rather than reimplementing them.

2. **Configure per environment**: Set environment variables in development, staging, and production to tune behavior without code changes.

3. **Document your configuration**: Maintain a list of environment variables your deployment uses for operational clarity.

4. **Test with different values**: Verify your application works with various configuration values, especially pagination limits and timeouts.

5. **Don't import const directly**: The const module is for internal framework use. Configure behavior through environment variables, not by importing constants.

6. **Timezone configuration**: Set `JARARACA_DEFAULT_TIMEZONE` to your application's timezone (e.g., "America/New_York

3. **Document your overrides**: Always document which environment variables your application uses.

4. **Validate ranges**: When using constants in Field validators, ensure they make sense (e.g., max >= default).

5. **Centralize configuration**: Import and use `const` rather than hardcoding values throughout your application.

6. **Timezone configuration**: The `DEFAULT_TIMEZONE` setting affects the beat scheduler's cron verification. Set it to your application's timezone (e.g., "America/New_York", "Europe/London") to ensure scheduled jobs run at the correct local times.

## Scheduler Timezone Configuration

The beat scheduler uses `DEFAULT_TIMEZONE` for cron job verification. This ensures scheduled actions respect your local timezone:

```bash
# Run scheduler with New York timezone
export JARARACA_DEFAULT_TIMEZONE=America/New_York
jararaca scheduler app:app --broker-url "amqp://..."
```

The scheduler will use this timezone when calculating the next run time for cron-based scheduled actions. Valid timezone names follow the IANA Time Zone Database format (e.g., "UTC", "America/New_York", "Europe/Paris", "Asia/Tokyo").

## Implementation Details

The const module is an internal framework component that:

- Reads configuration from environment variables at import time
- Provides type-safe defaults throughout the framework
- Powers classes like `PaginatedFilter`, `RetryPolicy`, connection pools, etc.
- Supports integer, float, boolean, and string values with fallback defaults

**For Framework Contributors**: Constants are defined in `src/jararaca/const.py` and use `env_parse_utils` for parsing. They should be used at class/function declaration level to set framework defaults.

**For Library Users**: You don't need to import or use `const` directly. Configure the framework behavior by setting environment variables and extending framework-provided classes.
