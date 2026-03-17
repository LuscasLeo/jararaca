# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Static configuration module for Jararaca framework.

This module defines library-wide constants that can be configured via environment variables.
These constants act at the class and function declaration layer, allowing configuration
to be applied before runtime instantiation.

Example usage:
    ```python
    from jararaca.const import PAGINATION_DEFAULT_PAGE_SIZE, PAGINATION_MAX_PAGE_SIZE
    from pydantic import BaseModel, Field
    from typing import Annotated

    class MyFilter(BaseModel):
        page_size: Annotated[int, Field(gt=0, le=PAGINATION_MAX_PAGE_SIZE)] = PAGINATION_DEFAULT_PAGE_SIZE
    ```
"""

from jararaca.utils.env_parse_utils import (
    get_abs_env_bool,
    get_env_float,
    get_env_int,
    get_env_str,
)

# ==========================================
# Pagination Configuration
# ==========================================

# Default number of items per page for paginated queries
# Used in PaginatedFilter and other pagination utilities
PAGINATION_DEFAULT_PAGE_SIZE: int = get_env_int(
    "JARARACA_PAGINATION_DEFAULT_PAGE_SIZE", default=10
)

# Maximum allowed page size to prevent excessive memory usage
# Can be used to set Field(le=PAGINATION_MAX_PAGE_SIZE) constraints
PAGINATION_MAX_PAGE_SIZE: int = get_env_int(
    "JARARACA_PAGINATION_MAX_PAGE_SIZE", default=100
)

# Default starting page index (0-based)
PAGINATION_DEFAULT_PAGE: int = get_env_int(
    "JARARACA_PAGINATION_DEFAULT_PAGE", default=0
)

# ==========================================
# Database Configuration
# ==========================================

# SQLAlchemy connection pool size
DB_POOL_SIZE: int = get_env_int("JARARACA_DB_POOL_SIZE", default=5)

# SQLAlchemy max overflow connections
DB_MAX_OVERFLOW: int = get_env_int("JARARACA_DB_MAX_OVERFLOW", default=10)

# SQLAlchemy pool timeout in seconds
DB_POOL_TIMEOUT: float = get_env_float("JARARACA_DB_POOL_TIMEOUT", default=30.0)

# SQLAlchemy pool recycle time in seconds (recycle connections after this time)
DB_POOL_RECYCLE: int = get_env_int("JARARACA_DB_POOL_RECYCLE", default=3600)

# Enable SQL query echo/logging
DB_ECHO: bool = (
    False  # Override via config, not recommended for env var due to verbosity
)

# ==========================================
# HTTP RPC Client Configuration
# ==========================================

# Default timeout for HTTP RPC requests in seconds
HTTP_RPC_DEFAULT_TIMEOUT: float = get_env_float(
    "JARARACA_HTTP_RPC_DEFAULT_TIMEOUT", default=30.0
)

# Default retry attempts for HTTP RPC requests
HTTP_RPC_DEFAULT_RETRY_ATTEMPTS: int = get_env_int(
    "JARARACA_HTTP_RPC_DEFAULT_RETRY_ATTEMPTS", default=3
)

# Default backoff factor for HTTP RPC retries
HTTP_RPC_DEFAULT_RETRY_BACKOFF: float = get_env_float(
    "JARARACA_HTTP_RPC_DEFAULT_RETRY_BACKOFF", default=1.0
)

# ==========================================
# Retry Policy Configuration
# ==========================================

# Default maximum retry attempts for generic retry operations
RETRY_DEFAULT_MAX_RETRIES: int = get_env_int(
    "JARARACA_RETRY_DEFAULT_MAX_RETRIES", default=5
)

# Default initial delay between retries in seconds
RETRY_DEFAULT_INITIAL_DELAY: float = get_env_float(
    "JARARACA_RETRY_DEFAULT_INITIAL_DELAY", default=1.0
)

# Default maximum delay between retries in seconds
RETRY_DEFAULT_MAX_DELAY: float = get_env_float(
    "JARARACA_RETRY_DEFAULT_MAX_DELAY", default=60.0
)

# Default backoff factor for exponential backoff
RETRY_DEFAULT_BACKOFF_FACTOR: float = get_env_float(
    "JARARACA_RETRY_DEFAULT_BACKOFF_FACTOR", default=2.0
)

# Default jitter setting for retry delays
RETRY_DEFAULT_JITTER: bool = True

# ==========================================
# Message Bus Worker Configuration
# ==========================================

# Default prefetch count for message bus consumers
MESSAGEBUS_DEFAULT_PREFETCH_COUNT: int = get_env_int(
    "JARARACA_MESSAGEBUS_DEFAULT_PREFETCH_COUNT", default=10
)

# Connection retry configuration for message bus
MESSAGEBUS_CONNECTION_RETRY_MAX: int = get_env_int(
    "JARARACA_MESSAGEBUS_CONNECTION_RETRY_MAX", default=15
)

MESSAGEBUS_CONNECTION_RETRY_INITIAL_DELAY: float = get_env_float(
    "JARARACA_MESSAGEBUS_CONNECTION_RETRY_INITIAL_DELAY", default=1.0
)

MESSAGEBUS_CONNECTION_RETRY_MAX_DELAY: float = get_env_float(
    "JARARACA_MESSAGEBUS_CONNECTION_RETRY_MAX_DELAY", default=60.0
)

MESSAGEBUS_CONNECTION_RETRY_BACKOFF: float = get_env_float(
    "JARARACA_MESSAGEBUS_CONNECTION_RETRY_BACKOFF", default=2.0
)

# Consumer retry configuration for message bus
MESSAGEBUS_CONSUMER_RETRY_MAX: int = get_env_int(
    "JARARACA_MESSAGEBUS_CONSUMER_RETRY_MAX", default=15
)

MESSAGEBUS_CONSUMER_RETRY_INITIAL_DELAY: float = get_env_float(
    "JARARACA_MESSAGEBUS_CONSUMER_RETRY_INITIAL_DELAY", default=0.5
)

MESSAGEBUS_CONSUMER_RETRY_MAX_DELAY: float = get_env_float(
    "JARARACA_MESSAGEBUS_CONSUMER_RETRY_MAX_DELAY", default=40.0
)

MESSAGEBUS_CONSUMER_RETRY_BACKOFF: float = get_env_float(
    "JARARACA_MESSAGEBUS_CONSUMER_RETRY_BACKOFF", default=2.0
)

# Connection health monitoring
MESSAGEBUS_CONNECTION_HEARTBEAT_INTERVAL: float = get_env_float(
    "JARARACA_MESSAGEBUS_CONNECTION_HEARTBEAT_INTERVAL", default=30.0
)

MESSAGEBUS_CONNECTION_HEALTH_CHECK_INTERVAL: float = get_env_float(
    "JARARACA_MESSAGEBUS_CONNECTION_HEALTH_CHECK_INTERVAL", default=10.0
)

# ==========================================
# Scheduler Configuration
# ==========================================

# Maximum concurrent jobs for scheduler
SCHEDULER_MAX_CONCURRENT_JOBS: int = get_env_int(
    "JARARACA_SCHEDULER_MAX_CONCURRENT_JOBS", default=10
)

# Beat worker connection retry configuration
SCHEDULER_CONNECTION_RETRY_MAX: int = get_env_int(
    "JARARACA_SCHEDULER_CONNECTION_RETRY_MAX", default=10
)

SCHEDULER_CONNECTION_RETRY_INITIAL_DELAY: float = get_env_float(
    "JARARACA_SCHEDULER_CONNECTION_RETRY_INITIAL_DELAY", default=2.0
)

SCHEDULER_CONNECTION_RETRY_MAX_DELAY: float = get_env_float(
    "JARARACA_SCHEDULER_CONNECTION_RETRY_MAX_DELAY", default=60.0
)

SCHEDULER_CONNECTION_RETRY_BACKOFF: float = get_env_float(
    "JARARACA_SCHEDULER_CONNECTION_RETRY_BACKOFF", default=2.0
)

# Beat worker dispatch retry configuration
SCHEDULER_DISPATCH_RETRY_MAX: int = get_env_int(
    "JARARACA_SCHEDULER_DISPATCH_RETRY_MAX", default=3
)

SCHEDULER_DISPATCH_RETRY_INITIAL_DELAY: float = get_env_float(
    "JARARACA_SCHEDULER_DISPATCH_RETRY_INITIAL_DELAY", default=1.0
)

SCHEDULER_DISPATCH_RETRY_MAX_DELAY: float = get_env_float(
    "JARARACA_SCHEDULER_DISPATCH_RETRY_MAX_DELAY", default=10.0
)

SCHEDULER_DISPATCH_RETRY_BACKOFF: float = get_env_float(
    "JARARACA_SCHEDULER_DISPATCH_RETRY_BACKOFF", default=2.0
)

# Scheduler health check intervals
SCHEDULER_CONNECTION_HEARTBEAT_INTERVAL: float = get_env_float(
    "JARARACA_SCHEDULER_CONNECTION_HEARTBEAT_INTERVAL", default=30.0
)

SCHEDULER_HEALTH_CHECK_INTERVAL: float = get_env_float(
    "JARARACA_SCHEDULER_HEALTH_CHECK_INTERVAL", default=15.0
)

# Scheduler connection timeouts
SCHEDULER_CONNECTION_WAIT_TIMEOUT: float = get_env_float(
    "JARARACA_SCHEDULER_CONNECTION_WAIT_TIMEOUT", default=300.0
)

# Scheduler pool configuration
SCHEDULER_MAX_POOL_SIZE: int = get_env_int(
    "JARARACA_SCHEDULER_MAX_POOL_SIZE", default=10
)

SCHEDULER_POOL_RECYCLE_TIME: float = get_env_float(
    "JARARACA_SCHEDULER_POOL_RECYCLE_TIME", default=3600.0
)

# ==========================================
# WebSocket Configuration
# ==========================================

# WebSocket connection timeout in seconds
WEBSOCKET_CONNECTION_TIMEOUT: float = get_env_float(
    "JARARACA_WEBSOCKET_CONNECTION_TIMEOUT", default=60.0
)

# WebSocket ping interval in seconds
WEBSOCKET_PING_INTERVAL: float = get_env_float(
    "JARARACA_WEBSOCKET_PING_INTERVAL", default=30.0
)

# WebSocket max message size in bytes
WEBSOCKET_MAX_MESSAGE_SIZE: int = get_env_int(
    "JARARACA_WEBSOCKET_MAX_MESSAGE_SIZE", default=1048576  # 1MB
)

# ==========================================
# Observability Configuration
# ==========================================

# Enable distributed tracing
OBSERVABILITY_TRACING_ENABLED: bool = False  # Set via configuration

# Trace sampling rate (0.0 to 1.0)
OBSERVABILITY_TRACE_SAMPLE_RATE: float = get_env_float(
    "JARARACA_OBSERVABILITY_TRACE_SAMPLE_RATE", default=1.0
)

# ==========================================
# General Configuration
# ==========================================

# Application environment (development, staging, production)
APP_ENVIRONMENT: str | None = get_env_str("JARARACA_APP_ENVIRONMENT", default=None)

# Enable debug mode
DEBUG: bool = get_abs_env_bool("JARARACA_DEBUG", default=False)

# Default timezone for datetime operations
DEFAULT_TIMEZONE: str = get_env_str("JARARACA_DEFAULT_TIMEZONE", default="UTC")


__all__ = [
    # Pagination
    "PAGINATION_DEFAULT_PAGE_SIZE",
    "PAGINATION_MAX_PAGE_SIZE",
    "PAGINATION_DEFAULT_PAGE",
    # Database
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "DB_POOL_TIMEOUT",
    "DB_POOL_RECYCLE",
    "DB_ECHO",
    # HTTP RPC
    "HTTP_RPC_DEFAULT_TIMEOUT",
    "HTTP_RPC_DEFAULT_RETRY_ATTEMPTS",
    "HTTP_RPC_DEFAULT_RETRY_BACKOFF",
    # Retry
    "RETRY_DEFAULT_MAX_RETRIES",
    "RETRY_DEFAULT_INITIAL_DELAY",
    "RETRY_DEFAULT_MAX_DELAY",
    "RETRY_DEFAULT_BACKOFF_FACTOR",
    "RETRY_DEFAULT_JITTER",
    # Message Bus
    "MESSAGEBUS_DEFAULT_PREFETCH_COUNT",
    "MESSAGEBUS_CONNECTION_RETRY_MAX",
    "MESSAGEBUS_CONNECTION_RETRY_INITIAL_DELAY",
    "MESSAGEBUS_CONNECTION_RETRY_MAX_DELAY",
    "MESSAGEBUS_CONNECTION_RETRY_BACKOFF",
    "MESSAGEBUS_CONSUMER_RETRY_MAX",
    "MESSAGEBUS_CONSUMER_RETRY_INITIAL_DELAY",
    "MESSAGEBUS_CONSUMER_RETRY_MAX_DELAY",
    "MESSAGEBUS_CONSUMER_RETRY_BACKOFF",
    "MESSAGEBUS_CONNECTION_HEARTBEAT_INTERVAL",
    "MESSAGEBUS_CONNECTION_HEALTH_CHECK_INTERVAL",
    # Scheduler
    "SCHEDULER_MAX_CONCURRENT_JOBS",
    "SCHEDULER_CONNECTION_RETRY_MAX",
    "SCHEDULER_CONNECTION_RETRY_INITIAL_DELAY",
    "SCHEDULER_CONNECTION_RETRY_MAX_DELAY",
    "SCHEDULER_CONNECTION_RETRY_BACKOFF",
    "SCHEDULER_DISPATCH_RETRY_MAX",
    "SCHEDULER_DISPATCH_RETRY_INITIAL_DELAY",
    "SCHEDULER_DISPATCH_RETRY_MAX_DELAY",
    "SCHEDULER_DISPATCH_RETRY_BACKOFF",
    "SCHEDULER_CONNECTION_HEARTBEAT_INTERVAL",
    "SCHEDULER_HEALTH_CHECK_INTERVAL",
    "SCHEDULER_CONNECTION_WAIT_TIMEOUT",
    "SCHEDULER_MAX_POOL_SIZE",
    "SCHEDULER_POOL_RECYCLE_TIME",
    # WebSocket
    "WEBSOCKET_CONNECTION_TIMEOUT",
    "WEBSOCKET_PING_INTERVAL",
    "WEBSOCKET_MAX_MESSAGE_SIZE",
    # Observability
    "OBSERVABILITY_TRACING_ENABLED",
    "OBSERVABILITY_TRACE_SAMPLE_RATE",
    # General
    "APP_ENVIRONMENT",
    "DEBUG",
    "DEFAULT_TIMEZONE",
]
