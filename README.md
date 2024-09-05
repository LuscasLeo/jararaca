<img src="docs/assets/_f04774c9-7e05-4da4-8b17-8be23f6a1475.jpeg" alt="README.md" width="250" float="right">

# Jararaca Microservice Framework

## Overview

Jararaca is a aio-first microservice framework that provides a set of tools to build and deploy microservices in a simple and clear way.

## Features

### Hexagonal Architecture

The framework is based on the hexagonal architecture, which allows you to separate the business logic from the infrastructure, making the code more testable and maintainable.

### Dependency Injection

The framework uses the dependency injection pattern to manage the dependencies between the components of the application.

### Web Server Port

The framework provides a web server that listens on a specific port and routes the requests to the appropriate handler. It uses [FastAPI](https://fastapi.tiangolo.com/) as the web framework.

### Message Bus

The framework provides a message bus that allows you to send messages between the components of the application. It uses [AIO Pika](https://aio-pika.readthedocs.io/) as the message broker worker and publisher.

## Installation

```bash
pip install jararaca
```

## Usage


### Create a Microservice
```python
# app.py
from jararaca import Microservice, create_http_server, create_messagebus_worker

app = Microservice(
    providers=[
        ProviderSpec(
            provide=Token[AppConfig],
            use_factory=AppConfig.provider,
        )
    ],
    controllers=[TasksController],
    interceptors=[
        AIOSqlAlchemySessionInterceptor(
            AIOSQAConfig(
                connection_name="default",
                url="sqlite+aiosqlite:///db.sqlite3",
            )
        ),
    ],
)

web_app = create_http_server(app)
messagebus_worker = create_messagebus_worker(app)

```

### Run as a Web Server
```bash
uvicorn app:web_app --reload
```

### Run as a Message Bus Worker
```bash
jararaca worker app:messagebus_worker
```