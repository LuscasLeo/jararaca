# Jararaca Test Suite

Comprehensive test suite for the Jararaca microservice framework using pytest.

## Running Tests

### Run all tests
```bash
poetry run pytest
```

### Run with verbose output
```bash
poetry run pytest -v
```

### Run with coverage report
```bash
poetry run pytest --cov=src/jararaca --cov-report=term-missing
```

### Run specific test file
```bash
poetry run pytest tests/test_expose_type.py -v
```

### Run specific test class
```bash
poetry run pytest tests/test_expose_type.py::TestExposeTypeDecorator -v
```

### Run specific test function
```bash
poetry run pytest tests/test_expose_type.py::TestExposeTypeDecorator::test_expose_type_decorator_marks_class -v
```

### Run tests by marker
```bash
# Run only unit tests
poetry run pytest -m unit

# Run only integration tests
poetry run pytest -m integration

# Run only slow tests
poetry run pytest -m slow
```

### Generate HTML coverage report
```bash
poetry run pytest --cov=src/jararaca --cov-report=html
# Open htmlcov/index.html in your browser
```

## Test Structure

```
tests/
├── conftest.py                      # Pytest configuration and fixtures
├── test_dependency_injection.py    # Tests for DI system (Token, ProviderSpec)
├── test_expose_type.py             # Tests for @ExposeType decorator
├── test_integration.py             # Integration tests for complete microservices
├── test_messagebus.py              # Tests for message bus decorators
├── test_microservice.py            # Tests for core Microservice class
├── test_rest_controllers.py        # Tests for REST controller decorators
├── test_typescript_decorators.py   # Tests for TypeScript generation decorators
└── test_typescript_generation.py   # Tests for TypeScript interface generation
```

## Test Categories

### Unit Tests
- **test_dependency_injection.py**: Token and ProviderSpec functionality
- **test_expose_type.py**: @ExposeType decorator behavior
- **test_typescript_decorators.py**: @SplitInputOutput, @QueryEndpoint, @MutationEndpoint
- **test_typescript_generation.py**: Type conversion and interface generation utilities
- **test_rest_controllers.py**: @RestController, @Get, @Post, etc.
- **test_messagebus.py**: @MessageBusController, @MessageHandler
- **test_microservice.py**: Core Microservice class

### Integration Tests
- **test_integration.py**: Complete microservice setups with multiple components

## Writing Tests

### Basic Test Structure

```python
import pytest
from pydantic import BaseModel

from jararaca import ExposeType


class TestMyFeature:
    """Test suite for my feature."""

    def test_basic_functionality(self) -> None:
        """Test description."""
        # Arrange
        @ExposeType()
        class MyModel(BaseModel):
            field: str

        # Act
        result = ExposeType.is_exposed_type(MyModel)

        # Assert
        assert result is True
```

### Async Tests

```python
class TestAsyncFeature:
    async def test_async_function(self) -> None:
        """Test async functionality."""
        result = await some_async_function()
        assert result == expected_value
```

### Using Fixtures

```python
def test_with_fixture(test_client) -> None:
    """Test using the test_client fixture."""
    response = test_client.get("/api/health")
    assert response.status_code == 200
```

### Test Markers

```python
@pytest.mark.unit
def test_unit() -> None:
    """A unit test."""

@pytest.mark.integration
def test_integration() -> None:
    """An integration test."""

@pytest.mark.slow
def test_slow_operation() -> None:
    """A slow test."""
```

## Coverage Goals

Current coverage: **18%** (base implementation)

Target areas for improvement:
- CLI commands (currently 0%)
- Worker and scheduler components (currently 0%)
- WebSocket backend (currently 0%)
- HTTP RPC client (currently 0%)
- Redis broker backend (currently 0%)

High-coverage areas:
- Core providers: **100%**
- ExposeType decorator: **100%**
- TypeScript decorators: **100%**
- HTTP microservice: **100%**

## Continuous Integration

Add to your CI/CD pipeline:

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install poetry
          poetry install
      - name: Run tests
        run: poetry run pytest --cov=src/jararaca --cov-report=xml
      - name: Upload coverage
        uses: codecov/codecov-action@v2
```

## Test Configuration

See `pyproject.toml` for pytest configuration:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
asyncio_mode = "auto"
addopts = ["--strict-markers", "--strict-config", "--showlocals"]
markers = [
    "unit: Unit tests",
    "integration: Integration tests",
    "slow: Slow tests",
]
```

## Best Practices

1. **Test Naming**: Use descriptive names that explain what is being tested
2. **One Assert Per Test**: Focus each test on a single behavior
3. **AAA Pattern**: Arrange, Act, Assert
4. **Type Annotations**: Always add return type annotations to test functions
5. **Docstrings**: Add docstrings to test classes and functions
6. **Isolation**: Tests should not depend on each other
7. **Fast Tests**: Keep unit tests fast (<1s each)
8. **Coverage**: Aim for >80% coverage on critical paths

## Troubleshooting

### Tests fail with import errors
```bash
# Ensure dependencies are installed
poetry install

# Ensure you're using poetry's environment
poetry run pytest
```

### Mypy errors in tests
The test files may show mypy errors related to the framework's internal typing. These are expected and don't affect test execution.

### Async test warnings
If you see warnings about async tests, ensure `pytest-asyncio` is installed:
```bash
poetry add --group dev pytest-asyncio
```

## Contributing

When adding new features:
1. Write tests first (TDD approach recommended)
2. Ensure all tests pass: `poetry run pytest`
3. Check coverage: `poetry run pytest --cov=src/jararaca`
4. Run type checking: `poetry run mypy src/`
5. Format code: `poetry run black . && poetry run isort .`
