# Changelog

## [Unreleased] - 2025-12-19

### Added
- Graceful shutdown handling system for async applications
  - Added `shutdown_race()` utility function for racing coroutines against shutdown signals
  - Added `wait_for_shutdown()` to await shutdown events
  - Added `is_shutting_down()` to check shutdown state
  - Added `request_shutdown()` to programmatically trigger shutdown
  - Integrated shutdown handling across worker, scheduler, and server modules

## [Unreleased] - 2025-12-17

### Changed
- Refactored decorator system for better type safety and Mypy compliance
  - `SingleDecorator` and `StackableDecorator` now use bounded TypeVars
  - Updated all decorators to use explicit type arguments where necessary
- Improved TypeScript interface generation
  - Added support for controller inheritance
  - Decorators like `@Get`, `@Post`, `@UseMiddleware` are now correctly inherited from base classes
  - Overridden methods without decorators inherit configuration from parent methods

### Fixed
- Fixed 53 Mypy type errors related to generic inheritance in decorators
- Fixed TypeScript generation for inherited controller methods

## [Unreleased] - 2025-11-05

### Added
- New HTTP middleware system for UoW context management
  - Added `UowContextMiddleware` for HTTP requests
  - Added `WebSocketUowContextMiddleware` for WebSocket connections
  - Alternative to existing dependency injection approach
  - Improved performance and cleaner separation of concerns

## [0.3.16] - 2025-10-07

### Added
- Comprehensive test suite implementation
  - Added pytest configuration and fixtures
  - Unit tests for dependency injection
  - Tests for @ExposeType decorator
  - Integration tests for microservice setup
  - Tests for message bus and REST controllers
  - TypeScript generation testing

### Added
- @ExposeType decorator for explicit TypeScript type exposure
  - Enhanced type generation control
  - Improved TypeScript interface generation

## [0.3.15] - 2025-09-15

### Added
- Enhanced TypeScript interface generation
  - Added recursiveCamelToSnakeCase utility
  - Improved support for RootModel types

## [0.3.14] - 2025-09-08

### Added
- Enhanced TypeScript interface parsing for RootModel types

### Removed
- Obsolete test scripts for HTTP RPC client

## [0.3.13] - 2025-08-14

### Added
- Introduced providing_app_type context manager
- Enhanced application type handling across modules

## [0.3.12] - 2025-07-30

### Added
- Enhanced form data handling with nested structures
- Improved unwrap_annotated_type functionality
- Added support for UploadFile handling
- Improved session management and transaction handling
- Added SplitInputOutput decorator for TypeScript interfaces
- Enhanced RabbitMQ connection resilience with retry logic
- Improved WebSocket connection handling

## [0.3.11] - 2025-05-31

### Added
- Retry mechanism with exponential backoff
  - Introduced RPCRetryPolicy for customizing retry behavior
  - Added retry_with_backoff utility function
  - Implemented retry decorators
- Enhanced CLI documentation
  - Added environment variables support
  - Improved command line options
- Improved RabbitMQ integration
  - Better error handling
  - Enhanced infrastructure management
  - Graceful shutdown handling
- Enhanced scheduler implementation
  - Distributed task scheduling
  - Improved backend support
  - Better message broker integration

### Changed
- Refactored message bus infrastructure
  - Consolidated worker infrastructure declaration
  - Enhanced error handling for RabbitMQ operations
- Updated scheduler implementation
  - Removed deprecated scheduler code
  - Enhanced BeatWorker with improved features

### Removed
- Deprecated v1 scheduler and message bus worker code
- Redundant infrastructure declaration methods

### Documentation
- Added comprehensive interceptors documentation
- Updated CLI documentation with environment variables
- Added retry mechanism documentation
- Enhanced scheduler and message bus documentation
- Improved WebSocket documentation

## [0.3.10] - 2025-05-26

### Added
- Enhanced PaginatedFilter functionality
- Improved query execution steps in QueryOperations

## [0.3.9] - 2025-05-01

### Added
- Enhanced TypeScript string literal parsing
- Improved boolean conversion in parse_literal_value function

For detailed information about changes, see the git commit history.
