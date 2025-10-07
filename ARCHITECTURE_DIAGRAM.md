# @ExposeType Decorator Architecture

## Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        User's Code                              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ @ExposeType() decorator
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ExposeType Class                            │
│  • Sets metadata on class                                       │
│  • Adds type to global _exposed_types set                      │
│  • Returns decorated class unchanged                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ Type stored in memory
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              ExposeType._exposed_types (set)                    │
│  {NotificationPreference, ThemeSettings, UserPermissionSet}     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ Called during TS generation
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│         write_microservice_to_typescript_interface()            │
│  1. Get all REST endpoint types                                 │
│  2. Get all WebSocket message types                             │
│  3. ✨ Get all ExposeType types ✨                              │
│  4. Process all types recursively                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ Output generation
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Generated TypeScript File                      │
│  • All endpoint types                                           │
│  • All WebSocket types                                          │
│  • ✨ All @ExposeType types ✨                                  │
│  • All nested dependencies                                      │
└─────────────────────────────────────────────────────────────────┘
```

## Component Interaction

```
┌──────────────┐
│  User Model  │ ──── @ExposeType() ────▶ Decorator adds to global set
└──────────────┘
       │
       │ Inherits
       ▼
┌──────────────┐
│  BaseModel   │ ──── parse_type_to_typescript_interface()
└──────────────┘
       │
       │ Converts to
       ▼
┌──────────────────────────────────────┐
│  TypeScript Interface                │
│  • Field names: snake_case → camelCase
│  • Types: Python → TypeScript        │
│  • Optional fields: ? marker         │
└──────────────────────────────────────┘
```

## Decorator Lifecycle

```
┌─────────────────┐
│ Class Definition│
│  class User(...) │
└────────┬────────┘
         │
         │ @ExposeType() applied
         ▼
┌─────────────────────────────┐
│ Decorator Execution         │
│  1. Set metadata            │
│  2. Add to global set       │
│  3. Return class            │
└────────┬────────────────────┘
         │
         │ Class is now "exposed"
         ▼
┌─────────────────────────────┐
│ Runtime - No Changes        │
│  • Normal Pydantic behavior │
│  • No performance impact    │
└────────┬────────────────────┘
         │
         │ Later: TypeScript generation
         ▼
┌─────────────────────────────┐
│ CLI: jararaca gen-tsi       │
│  • Reads global set         │
│  • Includes all types       │
│  • Generates interfaces     │
└─────────────────────────────┘
```

## Data Flow

```
Python Type System          ExposeType Registry        TypeScript Generator
─────────────────          ───────────────────        ────────────────────

@ExposeType()
class User:        ──────▶  _exposed_types.add()  ──────▶  mapped_types_set.update()
    id: str                      │                              │
    name: str                    │                              │
                                 │                              ▼
                                 │                    parse_type_to_typescript()
                                 │                              │
                                 │                              ▼
                                 │                    "export interface User {"
                                 │                    "  id: string;"
                                 │                    "  name: string;"
                                 │                    "}"
                                 │
                                 ▼
                        get_all_exposed_types()
                        returns: set[type]
```

## Integration Points

```
┌────────────────────────────────────────────────────────────────┐
│                      Jararaca Framework                         │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│  REST Endpoints          WebSocket Messages      @ExposeType   │
│       │                        │                      │         │
│       │                        │                      │         │
│       ▼                        ▼                      ▼         │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │      write_microservice_to_typescript_interface()       │   │
│  │  • Collects all type sources                            │   │
│  │  • Processes types recursively                          │   │
│  │  • Generates unified TypeScript output                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│                    TypeScript Output File                       │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

## Type Resolution

```
Input: @ExposeType decorated classes
  ↓
ExposeType._exposed_types
  ↓
mapped_types_set (TypeScript parser)
  ↓
Recursive type extraction
  ↓
  • BaseModel fields
  • Nested types
  • Generic arguments
  • Union types
  • Literal types
  ↓
TypeScript interface generation
  ↓
Output: Complete TypeScript interfaces
```

## Memory Layout

```
Module Load Time:
┌──────────────────────────────────────┐
│ ExposeType Class Definition          │
│  ├─ METADATA_KEY = "..."             │
│  └─ _exposed_types = set()           │ ◀── Empty set created
└──────────────────────────────────────┘

Decoration Time (Import):
┌──────────────────────────────────────┐
│ @ExposeType() applied to User        │
│  ├─ User.__jararaca_expose_type__    │ ◀── Metadata set
│  └─ _exposed_types.add(User)         │ ◀── Type tracked
└──────────────────────────────────────┘

Generation Time (CLI):
┌──────────────────────────────────────┐
│ jararaca gen-tsi command             │
│  ├─ get_all_exposed_types()          │ ◀── Reads global set
│  └─ Generate TypeScript              │
└──────────────────────────────────────┘
```

## Key Design Decisions

1. **Global Set Storage**: Types stored in class-level set for easy access
2. **Metadata Marking**: Each type also gets metadata attribute for checking
3. **Lazy Evaluation**: No processing at decoration time, only at generation
4. **Immutable After Decoration**: Decorator returns original class unchanged
5. **Composable**: Works with other decorators via standard Python decorator pattern
