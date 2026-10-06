# TypeScript Interface Generation

Jararaca provides a powerful tool to generate TypeScript interfaces and client code from your Python backend code. This ensures type safety across your full stack and reduces the need for manual type synchronization.

## Usage

To generate TypeScript interfaces, use the `gen-tsi` command:

```bash
jararaca gen-tsi APP_PATH OUTPUT_FILE
```

Example:
```bash
jararaca gen-tsi app:app src/client/api.ts
```

## Features

### Automatic Type Mapping
Pydantic models used in your controllers (request bodies, response models, query parameters) are automatically converted to TypeScript interfaces.

### Controller Inheritance
The generator supports inheritance in controllers. If you have a base controller with endpoints and middlewares, a child controller will inherit these configurations.

```python
@UseMiddleware(BaseMiddleware)
@RestController("/base")
class BaseController:
    @Get("/hello")
    async def hello(self) -> str:
        return "hello"

@RestController("/child")
class ChildController(BaseController):
    # Inherits /hello endpoint and BaseMiddleware
    pass
```

If you override a method in the child controller without adding new decorators, it will inherit the endpoint configuration (path, method, middlewares) from the parent class.

```python
@RestController("/child")
class ChildController(BaseController):
    async def hello(self) -> str:
        # Still a GET /child/hello endpoint
        return "child hello"
```

### Enum Style
By default Python enums are generated as TypeScript `enum`s. A TypeScript `enum` compiles to extra runtime code, so projects with many enums can shrink their final bundle by emitting a plain `as const` object plus a type alias instead:

```bash
jararaca gen-tsi app:app src/client/api.ts --enum-style const
```

```typescript
// --enum-style enum (default)
export enum Status {
	ACTIVE = "active",
	INACTIVE = "inactive",
}

// --enum-style const
export const Status = {
	ACTIVE: "active",
	INACTIVE: "inactive",
} as const;
export type Status = (typeof Status)[keyof typeof Status];
```

`Status` keeps working as both a value (`Status.ACTIVE`) and a type (`const s: Status`), and also in `switch` statements and `Object.values(Status)`. The one difference is that a member can no longer be used directly as a type: write `typeof Status.ACTIVE` instead of `Status.ACTIVE`. The generator already does this for `Literal[Status.ACTIVE]` fields. The option can also be set with the `ENUM_STYLE` environment variable.

### Explicit Type Exposure
Use the `@ExposeType` decorator to include types that aren't directly used in endpoints but are needed on the frontend. See [Exposing Types](expose-type.md) for more details.

### Query and Mutation Endpoints
Decorate methods with `@QueryEndpoint` or `@MutationEndpoint` to generate specific hooks for data fetching libraries (like React Query).

```python
@QueryEndpoint
@Get("/items")
async def get_items(self) -> list[Item]:
    ...
```
