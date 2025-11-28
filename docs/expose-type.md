# Exposing Types for TypeScript Generation

The `@ExposeType` decorator allows you to explicitly expose types for TypeScript interface generation without requiring them to be directly referenced in REST endpoints, WebSocket messages, or as indirect dependencies.

## Use Cases

The `@ExposeType` decorator is useful when you have types that:

1. **Are used only on the frontend** - Types that exist for frontend state management, validation, or business logic
2. **Are part of a shared schema** - Common types that multiple parts of your application use but aren't directly in API contracts
3. **Need to be pre-generated** - Types that you want available immediately even if they're not yet used in any endpoints
4. **Are utility types** - Helper types, enums, or constants that the frontend needs to know about

## Basic Usage

Simply decorate any Pydantic model with `@ExposeType()`:

```python
from pydantic import BaseModel

from jararaca import ExposeType


@ExposeType()
class UserPermission(BaseModel):
    id: str
    name: str
    description: str
    resource: str
    action: str
```

This type will now be included in the generated TypeScript output when you run:

```bash
jararaca gen-tsi app:app output.ts
```

## Example: Frontend-Only Types

```python
from pydantic import BaseModel

from jararaca import ExposeType


@ExposeType()
class FilterState(BaseModel):
    """Frontend state for table filtering."""
    search_query: str
    sort_column: str
    sort_direction: str
    page: int
    page_size: int

@ExposeType()
class UITheme(BaseModel):
    """Frontend theme configuration."""
    primary_color: str
    secondary_color: str
    dark_mode: bool
```

## Example: Error Codes and Constants

```python
from enum import Enum

from pydantic import BaseModel

from jararaca import ExposeType


@ExposeType()
class ErrorCode(str, Enum):
    """Standard error codes."""
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"

@ExposeType()
class ApiErrorDetail(BaseModel):
    """Detailed error information."""
    code: ErrorCode
    message: str
    field: str | None = None
    details: dict[str, str] | None = None
```

## Example: Complex Nested Types

```python
from pydantic import BaseModel

from jararaca import ExposeType


@ExposeType()
class Address(BaseModel):
    street: str
    city: str
    country: str
    postal_code: str

@ExposeType()
class ContactInfo(BaseModel):
    email: str
    phone: str | None = None
    address: Address

@ExposeType()
class Organization(BaseModel):
    """Complete organization structure."""
    id: str
    name: str
    contacts: list[ContactInfo]
    settings: dict[str, str]
```

When you expose a type with nested structures, the decorator ensures that all related types are also included in the TypeScript generation.

## Comparison: With vs Without @ExposeType

### Without @ExposeType

```python
class UserRole(BaseModel):
    """Only generated if used in an endpoint or as a dependency."""
    id: str
    name: str

@RestController("/api/users")
class UserController:
    @Get("/{user_id}")
    async def get_user(self, user_id: str) -> UserResponse:
        # UserRole is only generated if UserResponse references it
        return UserResponse(...)
```

### With @ExposeType

```python
@ExposeType()
class UserRole(BaseModel):
    """Always generated, available immediately."""
    id: str
    name: str

@RestController("/api/users")
class UserController:
    @Get("/{user_id}")
    async def get_user(self, user_id: str) -> UserResponse:
        # UserRole is available in TypeScript even if not used yet
        return UserResponse(...)
```

## Integration with Other Decorators

The `@ExposeType` decorator works seamlessly with other TypeScript generation decorators:

```python
from jararaca import ExposeType, SplitInputOutput


@ExposeType()
@SplitInputOutput()
class UserProfile(BaseModel):
    """Generates UserProfileInput and UserProfileOutput interfaces."""
    id: str
    username: str
    email: str
    created_at: str
    updated_at: str
```

This creates both `UserProfileInput` and `UserProfileOutput` TypeScript interfaces.

## Best Practices

1. **Use for shared types**: Apply `@ExposeType` to types that are used across multiple parts of your application
2. **Document the purpose**: Add clear docstrings explaining why a type is exposed
3. **Avoid overuse**: Only expose types that the frontend actually needs - don't expose internal implementation details
4. **Combine with other decorators**: Use alongside `@SplitInputOutput` when appropriate
5. **Group related types**: Keep exposed types in dedicated modules (e.g., `shared_types.py`)

## Viewing Exposed Types

All types decorated with `@ExposeType` are tracked globally. You can check which types are exposed:

```python
from jararaca.tools.typescript.decorators import ExposeType

# Get all exposed types
exposed = ExposeType.get_all_exposed_types()
print(f"Exposed {len(exposed)} types: {[t.__name__ for t in exposed]}")
```

## Generated TypeScript

Given this Python code:

```python
@ExposeType()
class NotificationPreference(BaseModel):
    email_enabled: bool
    push_enabled: bool
    frequency: str
```

The generated TypeScript will be:

```typescript
export interface NotificationPreference {
  emailEnabled: boolean;
  frequency: string;
  pushEnabled: boolean;
}
```

The type is available in your TypeScript code even if no REST endpoint uses it yet.

## Full Example

You can find a complete example in `examples/full_expose_example.py`. To generate the TypeScript interfaces for this example, run:

```bash
jararaca gen-tsi examples.full_expose_example:app --stdout
```

This will output:

```typescript
/* eslint-disable */
// @ts-nocheck
// noinspection JSUnusedGlobalSymbols

import { HttpService, HttpBackend, HttpBackendRequest, ResponseType, createClassQueryHooks , createClassMutationHooks, createClassInfiniteQueryHooks, paginationModelByFirstArgPaginationFilter, recursiveCamelToSnakeCase } from "@jararaca/core";

// ... helper functions ...

export interface NotificationPreference {
  emailEnabled: boolean;
  frequency: string;
  pushEnabled: boolean;
  smsEnabled: boolean;
}
export interface ThemeSettings {
  accentColor: string;
  darkMode: boolean;
  fontSize: string;
  primaryColor: string;
}
export interface UserListResponse {
  page: number;
  pageSize: number;
  total: number;
  users: Array<UserProfile>;
}
export interface UserPermissionSet {
  canAdmin: boolean;
  canDelete: boolean;
  canRead: boolean;
  canWrite: boolean;
  resourceType: string;
}
export interface UserProfileInput {
  avatarUrl?: string | null;
  displayName?: string | null;
  email: string;
  id: string;
  username: string;
}
export interface UserProfileOutput {
  avatarUrl: string | null;
  displayName: string | null;
  email: string;
  id: string;
  username: string;
}
export class UserController extends HttpService {
    async listUsers(page: number, page_size: number): Promise<UserListResponse> {
        const response = await this.httpBackend.request<UserListResponse>({
            method: "GET",
            path: `/api/users`,
            pathParams: {
            },
            headers: {
            },
            query: {
                "page": page,
                "page_size": page_size,
            },
            body: undefined
        });
        return response;
    }
}
```
