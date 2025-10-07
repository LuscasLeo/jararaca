# @ExposeType Decorator - Implementation Summary

## Overview

The `@ExposeType` decorator has been successfully implemented to allow library users to explicitly expose types for TypeScript generation without requiring them to be used in REST endpoints, WebSocket messages, or as indirect dependencies.

## Changes Made

### 1. Core Decorator (`src/jararaca/tools/typescript/decorators.py`)

Added the `ExposeType` class with:
- `__call__` method to decorate classes
- `is_exposed_type()` static method to check if a type is exposed
- `get_all_exposed_types()` static method to retrieve all exposed types
- Class-level `_exposed_types` set to track decorated types globally

### 2. TypeScript Parser Integration (`src/jararaca/tools/typescript/interface_parser.py`)

Modified `write_microservice_to_typescript_interface()` to:
- Import the `ExposeType` decorator
- Add all exposed types to `mapped_types_set` at the beginning of processing
- This ensures exposed types are included in the TypeScript output

### 3. Public API Export (`src/jararaca/__init__.py`)

Added `ExposeType` to:
- TYPE_CHECKING imports
- `__all__` export list
- Lazy import specification dictionary

### 4. Documentation

Created comprehensive documentation:
- **`docs/expose-type.md`**: Complete user guide with examples and best practices
- **`examples/expose_type_example.py`**: Simple usage examples
- **`examples/full_expose_example.py`**: Complete microservice integration example
- **`test_expose_type.py`**: Verification test script

Updated **`mkdocs.yml`** to include the new documentation page.

## Usage

```python
from pydantic import BaseModel

from jararaca import ExposeType


@ExposeType()
class UserPermission(BaseModel):
    id: str
    name: str
    resource: str
    action: str
```

The decorated type will be included in TypeScript generation:

```bash
jararaca gen-tsi app:app output.ts
```

## Key Features

1. **Simple API**: Single decorator, no configuration required
2. **Type Safety**: Fully typed with mypy strict mode compliance
3. **Global Tracking**: All exposed types tracked in a class-level set
4. **Composable**: Works with other decorators like `@SplitInputOutput`
5. **Zero Runtime Overhead**: Only affects TypeScript generation, not runtime behavior

## Verification

All tests pass:
```bash
✅ All tests passed!
Found 2 exposed types:
  - ExampleType1
  - ExampleType2
```

## Integration

The decorator integrates seamlessly with existing Jararaca features:
- Works alongside REST endpoint types
- Compatible with `@SplitInputOutput` decorator
- Respects all existing TypeScript generation rules
- No breaking changes to existing functionality

## Files Modified

1. `src/jararaca/tools/typescript/decorators.py` - Added `ExposeType` class
2. `src/jararaca/tools/typescript/interface_parser.py` - Added import and integration
3. `src/jararaca/__init__.py` - Added public API export
4. `mkdocs.yml` - Added documentation section

## Files Created

1. `docs/expose-type.md` - User documentation
2. `examples/expose_type_example.py` - Simple examples
3. `examples/full_expose_example.py` - Complete integration example
4. `test_expose_type.py` - Verification tests

## Next Steps

Users can now:
1. Decorate any Pydantic model with `@ExposeType()`
2. Generate TypeScript interfaces with `jararaca gen-tsi`
3. Use the exposed types in frontend code immediately
4. Check exposed types with `ExposeType.get_all_exposed_types()`
