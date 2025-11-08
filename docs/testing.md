# Testing in Jararaca

Jararaca includes a comprehensive test suite and provides utilities for testing your microservice applications. This guide covers how to test your Jararaca applications effectively.

## Running Framework Tests

The Jararaca framework itself comes with a full test suite:

```bash
# Run all tests
poetry run pytest

# Run with coverage
poetry run pytest --cov=src/jararaca --cov-report=html

# Run specific test categories
poetry run pytest -m unit          # Unit tests only
poetry run pytest -m integration   # Integration tests only
```

## Testing Your Application

### Basic Test Setup

Create a pytest fixture for your microservice:

```python
import pytest

from jararaca import AIOSQAConfig, AIOSqlAlchemySessionInterceptor, Microservice


@pytest.fixture
async def app():
    """Create a test microservice instance."""
    return Microservice(
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
        controllers=[YourController],
    )
```

### Testing REST Controllers

Use FastAPI's TestClient for testing HTTP endpoints:

```python
import pytest
from httpx import AsyncClient
from jararaca import HttpMicroservice, create_http_server

@pytest.fixture
async def client(app):
    """Create test HTTP client."""
    http_app = HttpMicroservice(app=app)
    server = create_http_server(http_app)

    async with AsyncClient(app=server, base_url="http://test") as client:
        yield client

@pytest.mark.asyncio
async def test_create_user(client):
    """Test user creation endpoint."""
    response = await client.post(
        "/api/users",
        json={"name": "John Doe", "email": "john@example.com"}
    )

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "John Doe"
    assert data["email"] == "john@example.com"
```

### Testing Message Bus Handlers

Test message handlers by invoking them directly:

```python
import pytest
from jararaca import MessageOf, use_session
from your_app.events import UserCreatedEvent
from your_app.handlers import UserEventsController

@pytest.mark.asyncio
async def test_user_created_handler(app):
    """Test user created event handler."""
    # Create handler instance
    handler = UserEventsController()

    # Create test message
    event = UserCreatedEvent(user_id="123", name="John")
    message = MessageOf(body=event, topic="user.created")

    # Execute handler
    await handler.handle_user_created(message)

    # Verify results
    session = use_session()
    # Assert your expectations
```

### Testing Scheduled Actions

Test scheduled actions directly:

```python
import pytest
from your_app.tasks import ScheduledTasksController

@pytest.mark.asyncio
async def test_cleanup_task(app):
    """Test cleanup scheduled task."""
    controller = ScheduledTasksController()

    # Execute the scheduled action
    await controller.cleanup_old_data()

    # Verify cleanup was performed
    session = use_session()
    # Assert your expectations
```

### Testing Database Operations

Test database operations with in-memory SQLite:

```python
import pytest
from jararaca import use_session
from your_app.models import User

@pytest.mark.asyncio
async def test_user_repository(app):
    """Test user repository operations."""
    session = use_session()

    # Create test user
    user = User(id="1", name="Test User", email="test@example.com")
    session.add(user)
    await session.flush()

    # Query user
    from sqlalchemy import select
    result = await session.execute(select(User).where(User.id == "1"))
    found_user = result.scalar_one()

    assert found_user.name == "Test User"
    assert found_user.email == "test@example.com"
```

### Testing with Dependency Injection

Test components with dependency injection:

```python
import pytest
from jararaca import Container, Microservice, ProviderSpec, Token

@pytest.mark.asyncio
async def test_with_di():
    """Test component with dependency injection."""
    # Create mock service
    class MockEmailService:
        async def send(self, to: str, message: str):
            return True

    # Configure app with mock
    app = Microservice(
        name="test",
        providers=[
            ProviderSpec(
                provide=Token(EmailService, "EMAIL_SERVICE"),
                use_value=MockEmailService()
            )
        ],
        controllers=[YourController]
    )

    # Test your controller
    container = Container(app)
    controller = container.get_by_type(YourController)

    result = await controller.send_notification(
        user_id="123",
        message="Test"
    )

    assert result is True
```

### Testing WebSocket Endpoints

Test WebSocket connections:

```python
import pytest
from httpx import AsyncClient
from fastapi.testclient import TestClient

@pytest.mark.asyncio
async def test_websocket_endpoint(app):
    """Test WebSocket connection."""
    http_app = create_http_server(HttpMicroservice(app=app))

    with TestClient(http_app) as client:
        with client.websocket_connect("/ws/chat") as websocket:
            # Send message
            websocket.send_json({"type": "message", "content": "Hello"})

            # Receive response
            data = websocket.receive_json()
            assert data["type"] == "response"
```

### Testing with Interceptors

Test that interceptors work correctly:

```python
import pytest
from jararaca import use_session, use_publisher

@pytest.mark.asyncio
async def test_transactional_outbox(app):
    """Test that messages are only published if DB transaction succeeds."""
    session = use_session()
    publisher = use_publisher()

    # Create entity
    user = User(id="1", name="Test")
    session.add(user)

    # Stage message
    await publisher.publish(UserCreatedEvent(user_id="1"))

    # At this point, message is staged but not published
    # Transaction will commit and message will be published
```

### Mocking External Dependencies

Use pytest fixtures to mock external services:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def mock_rabbitmq():
    """Mock RabbitMQ connection."""
    mock = AsyncMock()
    mock.publish = AsyncMock(return_value=True)
    return mock

@pytest.fixture
def mock_redis():
    """Mock Redis connection."""
    mock = MagicMock()
    mock.get = MagicMock(return_value=b'{"data": "cached"}')
    return mock

@pytest.mark.asyncio
async def test_with_mocks(app, mock_rabbitmq, mock_redis):
    """Test with mocked dependencies."""
    # Your test using mocked dependencies
```

## Test Organization

### Directory Structure

```
tests/
├── conftest.py              # Shared fixtures
├── test_controllers.py      # REST controller tests
├── test_messagebus.py       # Message bus handler tests
├── test_scheduler.py        # Scheduled task tests
├── test_repositories.py     # Database operation tests
├── test_services.py         # Business logic tests
└── test_integration.py      # End-to-end integration tests
```

### Using Markers

Organize tests with pytest markers:

```python
import pytest

@pytest.mark.unit
def test_business_logic():
    """Fast unit test."""

@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_workflow():
    """Slower integration test."""

@pytest.mark.slow
@pytest.mark.asyncio
async def test_performance():
    """Performance test."""
```

Configure markers in `pytest.ini`:

```ini
[pytest]
markers =
    unit: Fast unit tests
    integration: Integration tests
    slow: Slow running tests
    asyncio: Async tests
```

## Best Practices

1. **Use In-Memory Databases**: SQLite in-memory for fast tests
2. **Isolate Tests**: Each test should be independent
3. **Mock External Services**: Don't make real HTTP calls or connect to external services
4. **Test Happy and Error Paths**: Cover both success and failure scenarios
5. **Use Fixtures**: Reuse common setup with pytest fixtures
6. **Async Tests**: Use `@pytest.mark.asyncio` for async tests
7. **Clear Assertions**: Make assertions specific and clear
8. **Test Coverage**: Aim for high coverage but focus on important paths

## Common Testing Patterns

### Testing Error Handling

```python
import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_error_handling(client):
    """Test that errors are handled correctly."""
    response = await client.post(
        "/api/users",
        json={"invalid": "data"}
    )

    assert response.status_code == 422
    assert "validation error" in response.json()["detail"]
```

### Testing Authentication

```python
import pytest


@pytest.mark.asyncio
async def test_requires_auth(client):
    """Test that endpoint requires authentication."""
    response = await client.get("/api/protected")
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_with_auth(client):
    """Test authenticated request."""
    response = await client.get(
        "/api/protected",
        headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 200
```

### Testing Pagination

```python
import pytest


@pytest.mark.asyncio
async def test_pagination(client):
    """Test paginated endpoint."""
    # Create test data
    for i in range(25):
        await client.post("/api/items", json={"name": f"Item {i}"})

    # Test first page
    response = await client.get("/api/items?page=1&page_size=10")
    data = response.json()

    assert len(data["items"]) == 10
    assert data["total"] == 25
    assert data["page"] == 1
```

### Testing with Transaction Rollback

```python
import pytest

from jararaca import use_session


@pytest.mark.asyncio
async def test_with_rollback(app):
    """Test that changes are rolled back on error."""
    session = use_session()

    try:
        user = User(id="1", name="Test")
        session.add(user)
        await session.flush()

        # Simulate error
        raise ValueError("Something went wrong")
    except ValueError:
        await session.rollback()

    # Verify rollback
    from sqlalchemy import select
    result = await session.execute(select(User).where(User.id == "1"))
    assert result.scalar_one_or_none() is None
```

## Continuous Integration

Example GitHub Actions workflow:

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v2

      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install poetry
          poetry install

      - name: Run tests
        run: |
          poetry run pytest --cov=src --cov-report=xml

      - name: Upload coverage
        uses: codecov/codecov-action@v2
        with:
          file: ./coverage.xml
```

## Conclusion

Jararaca provides a solid foundation for testing microservice applications. By following these patterns and best practices, you can build a comprehensive test suite that ensures your application works correctly across all components: HTTP APIs, message handlers, scheduled tasks, and database operations.
