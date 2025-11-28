# Metadata and Decorators

Jararaca provides a robust system for handling metadata and creating custom decorators that preserve context across the application lifecycle. This system is particularly useful for implementing cross-cutting concerns like authorization, logging, and transaction management.

## Metadata System

The metadata system allows you to attach arbitrary data to your controllers and methods, which can then be accessed during the request lifecycle.

### Setting Metadata

You can use the `SetMetadata` decorator to attach metadata to a class or method.

```python
from jararaca.reflect.metadata import SetMetadata


@SetMetadata("role", "admin")
class AdminController:
    pass
```

### Accessing Metadata

Metadata is stored in a context-aware storage that propagates through the request lifecycle. You can access it using helper functions:

```python
from jararaca.reflect.metadata import get_metadata, get_metadata_value

# Get the full metadata object
metadata = get_metadata("role")
if metadata:
    print(metadata.value)  # "admin"
    print(metadata.inherited_from_controller)  # False

# Get just the value (with optional default)
role = get_metadata_value("role", default="guest")
```

### Transaction Metadata Context

The metadata system is built on top of `ContextVar` and is designed to work within the Unit of Work (UoW) context. When a request starts, the framework initializes a metadata context that includes:

1. Metadata defined on the controller class
2. Metadata defined on the handler method
3. Dynamic metadata added during execution

You can manually manage this context using `start_transaction_metadata_context` or `start_providing_metadata`:

```python
from jararaca.reflect.metadata import start_providing_metadata


async def background_task():
    # Start a new metadata context
    with start_providing_metadata(request_id="123"):
        # Code here can access "request_id" via get_metadata_value
        process_data()
```

## Custom Decorators

When creating custom decorators for your controllers, it's crucial to preserve the metadata and controller member information. Jararaca provides utilities to help with this.

### Composing Decorators

The `compose_route_decorators` function allows you to combine multiple middlewares and dependencies into a single reusable decorator.

```python
from jararaca.presentation.decorators import compose_route_decorators, UseMiddleware
from my_app.middleware import AuthMiddleware, RateLimitMiddleware

# Create a composite decorator
RequireAdmin = compose_route_decorators(
    UseMiddleware(AuthMiddleware),
    UseMiddleware(RateLimitMiddleware),
)

@RestController("/admin")
class AdminController:
    @Get("/users")
    @RequireAdmin  # Applies both middlewares
    async def get_users(self):
        pass
```

### Preserving Controller Context

If you're writing a decorator that wraps the handler function, you must ensure that the controller member context is preserved. Use `wraps_with_member_data` or `providing_controller_member` for this purpose.

```python
from functools import wraps

from jararaca.presentation.decorators import (
    providing_controller_member,
    use_controller_member,
)


def MyCustomDecorator(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Access current controller member info
        member = use_controller_member()
        print(f"Executing {member.member_function.__name__}")

        # If you need to execute the function in a new context (e.g. background task)
        # you should re-provide the member context
        with providing_controller_member(member):
            return await func(*args, **kwargs)

    return wrapper
```

### `wraps_with_attributes`

For simple attribute copying, you can use `wraps_with_attributes`:

```python
from jararaca.presentation.decorators import wraps_with_attributes


def Tag(name: str):
    def decorator(func):
        return wraps_with_attributes(func, __tag__=name)
    return decorator
```
