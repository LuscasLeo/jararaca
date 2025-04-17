<img src="https://raw.githubusercontent.com/LuscasLeo/jararaca/main/docs/assets/_f04774c9-7e05-4da4-8b17-8be23f6a1475.jpeg" alt="Jararaca Logo" width="250" float="right">

# Jararaca Microservice Framework

## Overview

Jararaca is an async-first microservice framework designed to simplify the development of distributed systems. It provides a comprehensive set of tools for building robust, scalable, and maintainable microservices with a focus on developer experience and type safety.

## Key Features

### REST API Development
- Easy-to-use interfaces for building REST APIs
- Automatic request/response validation
- Type-safe endpoints with FastAPI integration
- Automatic OpenAPI documentation generation

### Message Bus Integration
- Topic-based message bus for event-driven architecture
- Support for both worker and publisher patterns
- Built-in message serialization and deserialization
- Easy integration with AIO Pika for RabbitMQ

### Distributed WebSocket
- Room-based WebSocket communication
- Distributed broadcasting across multiple backend instances
- Automatic message synchronization between instances
- Built-in connection management and room handling

### Task Scheduling
- Cron-based task scheduling
- Support for overlapping and non-overlapping tasks
- Distributed task execution
- Easy integration with message bus for task distribution

### TypeScript Integration
- Automatic TypeScript interface generation
- Command-line tool for generating TypeScript types
- Support for REST endpoints, WebSocket events, and message bus payloads
- Type-safe frontend-backend communication

### Hexagonal Architecture
- Clear separation of concerns
- Business logic isolation from infrastructure
- Easy testing and maintainability
- Dependency injection for flexible component management

### Observability
- Built-in OpenTelemetry integration
- Distributed tracing support
- Logging and metrics collection
- Performance monitoring capabilities

## Quick Start

### Installation

```bash
pip install jararaca
```

### Basic Usage

```python
from jararaca import Microservice, create_http_server
from jararaca.presentation.http_microservice import HttpMicroservice

# Define your microservice
app = Microservice(
    providers=[
        # Add your providers here
    ],
    controllers=[
        # Add your controllers here
    ],
    interceptors=[
        # Add your interceptors here
    ],
)

# Create HTTP server
http_app = HttpMicroservice(app)
web_app = create_http_server(app)
```

### Running the Service

```bash
# Run as HTTP server
jararaca server app:http_app

# Run as message bus worker
jararaca worker app:app

# Run as scheduler
jararaca scheduler app:app

# Generate TypeScript interfaces
jararaca gen-tsi app.main:app app.ts
```

## Documentation

For detailed documentation, please visit our [documentation site](https://luscasleo.github.io/jararaca/).

## Examples

Check out the [examples directory](examples/) for complete working examples of:
- REST API implementation
- WebSocket usage
- Message bus integration
- Task scheduling
- TypeScript interface generation

## Contributing

Contributions are welcome! Please read our [contributing guidelines](.github/CONTRIBUTING.md) for details on our code of conduct and the process for submitting pull requests.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
