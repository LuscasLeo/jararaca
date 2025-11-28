# Database Persistence

Jararaca provides robust database persistence capabilities through SQLAlchemy with async support. The framework includes session management, transaction handling, and automatic integration with the interceptor system for atomic operations.

## Core Components

### 1. AIOSqlAlchemySessionInterceptor

The main interceptor that provides database session management across all application contexts (HTTP, Message Bus, Scheduler).

```python
from jararaca import AIOSQAConfig, AIOSqlAlchemySessionInterceptor, Microservice

app = Microservice(
    name="my-service",
    interceptors=[
        AIOSqlAlchemySessionInterceptor(
            AIOSQAConfig(
                connection_name="default",
                url="postgresql+asyncpg://user:password@localhost/mydb",
                inject_default=True,
            )
        )
    ],
    controllers=[...]
)
```

### 2. Session Context Management

Jararaca provides context-based session management that ensures proper transaction handling:

#### `use_session()` - Get Current Session

Access the current database session from anywhere in your application:

```python
from jararaca import use_session


async def create_user(user_data: dict):
    session = use_session()

    new_user = User(**user_data)
    session.add(new_user)

    # Transaction is automatically committed by the interceptor
    return new_user
```

#### `providing_new_session()` - Spawn New Transaction

Create a new independent session within the current transaction context:

```python
from jararaca import providing_new_session


async def create_audit_log(action: str):
    # This creates a new session independent of the main transaction
    async with providing_new_session() as audit_session:
        audit_log = AuditLog(action=action)
        audit_session.add(audit_log)
        # This session commits independently
```

**Use cases for `providing_new_session()`:**
- Creating audit logs that should persist even if the main transaction fails
- Batch operations that need independent commits
- Background tasks that need their own transaction scope

### 3. SessionManager Protocol

The `SessionManager` protocol defines the interface for session management:

```python
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class SessionManager(Protocol):
    def spawn_session(self, connection_name: str | None = None) -> AsyncSession: ...
```

Access the session manager directly when needed:

```python
# use_session_manager is not exported by jararaca, so we import it from the module
from jararaca.persistence.interceptors.aiosqa_interceptor import use_session_manager


async def advanced_session_handling():
    session_manager = use_session_manager()

    # Spawn a new session for a specific connection
    new_session = session_manager.spawn_session("secondary_db")
```

## Entity Models

### BaseEntity

All entity models should inherit from `BaseEntity`:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from jararaca import BaseEntity


class User(BaseEntity):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, unique=True)
```

### DatedEntity

For entities with timestamp tracking:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from jararaca import DatedEntity


class Article(DatedEntity):
    __tablename__ = "articles"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)
    # created_at is set on creation
    # updated_at is set on creation and automatically updated on modification
```

### IdentifiableEntity

For entities with UUID primary keys:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from jararaca import IdentifiableEntity


class Product(IdentifiableEntity):
    __tablename__ = "products"

    # id is automatically provided as UUID
    name: Mapped[str] = mapped_column(String)
    price: Mapped[float]
```

## Pydantic Integration

### Converting Between Models

BaseEntity provides methods for seamless Pydantic integration:

```python
from pydantic import BaseModel


class UserSchema(BaseModel):
    id: str
    name: str
    email: str

# Entity to Pydantic
user_entity = User(id="1", name="John", email="john@example.com")
user_schema = user_entity.to_basemodel(UserSchema)

# Pydantic to Entity
user_data = UserSchema(id="2", name="Jane", email="jane@example.com")
user_entity = User.from_basemodel(user_data)
```

## Multiple Database Connections

Configure multiple database connections for different purposes:

```python
app = Microservice(
    name="my-service",
    interceptors=[
        # Primary database
        AIOSqlAlchemySessionInterceptor(
            AIOSQAConfig(
                connection_name="default",
                url="postgresql+asyncpg://user:pass@localhost/main_db",
                inject_default=True,
            )
        ),
        # Analytics database
        AIOSqlAlchemySessionInterceptor(
            AIOSQAConfig(
                connection_name="analytics",
                url="postgresql+asyncpg://user:pass@localhost/analytics_db",
            )
        ),
    ],
)
```

Access different connections:

```python
# Default connection
session = use_session()

# Specific connection
analytics_session = use_session("analytics")
```

## Transaction Management

### Automatic Transaction Handling

By default, transactions are automatically managed by the interceptor:

```python
@RestController("/api/users")
class UserController:
    @Post("/")
    async def create_user(self, user: UserCreate) -> UserResponse:
        session = use_session()

        user_entity = User.from_basemodel(user)
        session.add(user_entity)

        # Transaction commits automatically at the end of the request
        return user_entity.to_basemodel(UserResponse)
```

### Manual Transaction Control

For explicit control over transactions:

```python
async def complex_operation():
    session = use_session()

    try:
        # Your operations
        user = User(name="John")
        session.add(user)

        # Explicit commit
        await session.commit()
    except Exception as e:
        # Explicit rollback
        await session.rollback()
        raise
```

### Nested Sessions

Create nested transactions for complex operations:

```python
async def main_operation():
    session = use_session()

    # Main operation
    user = User(name="John")
    session.add(user)

    # Independent nested transaction
    async with providing_new_session() as nested_session:
        audit = AuditLog(action="user_created")
        nested_session.add(audit)
        # Commits independently

    # Main transaction continues
```

## Query Operations

Jararaca provides utilities for common query patterns:

### QueryOperations

Base class for query operations with filtering and pagination:

```python
from sqlalchemy import select

from jararaca import QueryOperations


class UserQueries(QueryOperations[User, UserFilter]):
    async def list_users(self, filter: UserFilter):
        session = use_session()

        stmt = select(User)
        stmt = self.apply_filters(stmt, filter)

        result = await session.execute(stmt)
        return result.scalars().all()
```

### Pagination

Built-in pagination support:

```python
from jararaca import Paginated, PaginatedFilter


async def get_paginated_users(filter: PaginatedFilter):
    session = use_session()

    stmt = select(User)

    # Apply pagination
    total = await session.scalar(select(func.count()).select_from(User))
    stmt = stmt.offset(filter.offset).limit(filter.page_size)

    result = await session.execute(stmt)
    users = result.scalars().all()

    return Paginated(
        items=users,
        total=total,
        page=filter.page,
        page_size=filter.page_size
    )
```

## Integration with Transactional Outbox Pattern

The session interceptor automatically integrates with message publishing for transactional outbox:

```python
from jararaca import use_publisher, use_session


async def create_order(order_data: dict):
    session = use_session()
    publisher = use_publisher()

    # Create order in database
    order = Order(**order_data)
    session.add(order)

    # Stage message for publishing
    await publisher.publish(OrderCreatedEvent(order_id=order.id))

    # Order of execution:
    # 1. Database transaction commits
    # 2. If successful, message is published
    # 3. If DB fails, message is never published
```

## Configuration Options

### AIOSQAConfig

```python
@dataclass
class AIOSQAConfig:
    connection_name: str              # Unique identifier for the connection
    url: str                          # Database URL
    inject_default: bool = False      # Set as default connection
    echo: bool = False                # Enable SQL query logging
    pool_size: int = 5                # Connection pool size
    max_overflow: int = 10            # Max overflow connections
    pool_timeout: float = 30.0        # Pool timeout in seconds
    pool_recycle: int = 3600          # Recycle connections after N seconds
```

### Example with Full Configuration

```python
AIOSqlAlchemySessionInterceptor(
    AIOSQAConfig(
        connection_name="default",
        url="postgresql+asyncpg://user:pass@localhost/db",
        inject_default=True,
        echo=True,  # Enable SQL logging in development
        pool_size=10,
        max_overflow=20,
        pool_timeout=30.0,
        pool_recycle=3600,
    )
)
```

## Best Practices

1. **Use Context Hooks**: Always use `use_session()` to access the session instead of creating sessions manually
2. **Let Interceptor Manage Transactions**: Rely on automatic transaction management unless you have a specific reason not to
3. **Use `providing_new_session()` for Independent Operations**: Audit logs, background tasks, and operations that should succeed regardless of the main transaction
4. **Connection Names**: Use meaningful names for multiple connections (e.g., "default", "analytics", "cache")
5. **Entity Base Classes**: Inherit from `BaseEntity`, `DatedEntity`, or `IdentifiableEntity` for consistent behavior
6. **Pydantic Integration**: Use `to_basemodel()` and `from_basemodel()` for type-safe conversions

## Error Handling

```python
from sqlalchemy.exc import IntegrityError


async def create_user_safe(user_data: dict):
    session = use_session()

    try:
        user = User(**user_data)
        session.add(user)
        await session.flush()  # Check for errors before commit

        return user
    except IntegrityError as e:
        # Handle duplicate key, etc.
        await session.rollback()
        raise ValueError(f"User already exists: {e}")
```

## Testing

For testing, you can provide a test database configuration:

```python
import pytest

from jararaca import AIOSQAConfig, AIOSqlAlchemySessionInterceptor, Microservice


@pytest.fixture
async def test_app():
    app = Microservice(
        name="test-service",
        interceptors=[
            AIOSqlAlchemySessionInterceptor(
                AIOSQAConfig(
                    connection_name="test",
                    url="sqlite+aiosqlite:///:memory:",
                    inject_default=True,
                )
            )
        ],
    )
    return app
```

## Conclusion

Jararaca's persistence layer provides a powerful and flexible way to work with databases while maintaining consistency with message publishing and WebSocket communications through the transactional outbox pattern. The context-based session management ensures clean code and automatic transaction handling across all application contexts.
