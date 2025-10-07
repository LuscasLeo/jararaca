# ExposeType Decorator - Quick Reference

## Import

```python
from jararaca import ExposeType

```

## Basic Usage

```python
from pydantic import BaseModel

from jararaca import ExposeType


@ExposeType()
class MyType(BaseModel):
    field1: str
    field2: int
```

## Common Patterns

### 1. Frontend-Only Types

```python
@ExposeType()
class UIState(BaseModel):
    is_loading: bool
    selected_items: list[str]
    filter_text: str
```

### 2. Error Codes & Constants

```python
from enum import Enum


@ExposeType()
class ErrorCode(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
```

### 3. Combined with SplitInputOutput

```python
from jararaca import ExposeType, SplitInputOutput


@ExposeType()
@SplitInputOutput()
class User(BaseModel):
    id: str
    name: str
    email: str
```

Generates: `UserInput` and `UserOutput` interfaces

### 4. Shared Types

```python
@ExposeType()
class Address(BaseModel):
    street: str
    city: str
    country: str

@ExposeType()
class Contact(BaseModel):
    email: str
    address: Address  # Address will also be generated
```

## Generate TypeScript

```bash
jararaca gen-tsi app:app output.ts
```

## Check Exposed Types

```python
from jararaca.tools.typescript.decorators import ExposeType

# Get all exposed types
exposed = ExposeType.get_all_exposed_types()
print([t.__name__ for t in exposed])

# Check if a type is exposed
if ExposeType.is_exposed_type(MyType):
    print("MyType is exposed")
```

## When to Use

✅ **Use @ExposeType when:**
- Type is used only on the frontend
- Type is a shared utility/constant
- Type needs to be available before being used in endpoints
- Building a type library for frontend consumption

❌ **Don't use @ExposeType when:**
- Type is already used in REST endpoints (it will be auto-generated)
- Type is internal implementation detail
- Type contains sensitive backend-only information

## Example Output

**Python:**
```python
@ExposeType()
class Notification(BaseModel):
    id: str
    message: str
    read: bool
```

**Generated TypeScript:**
```typescript
export interface Notification {
  id: string;
  message: string;
  read: boolean;
}
```

## See Also

- [Full Documentation](docs/expose-type.md)
- [Example: expose_type_example.py](examples/expose_type_example.py)
- [Example: full_expose_example.py](examples/full_expose_example.py)
