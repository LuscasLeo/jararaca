<img src="https://raw.githubusercontent.com/LuscasLeo/jararaca/main/docs/assets/_f04774c9-7e05-4da4-8b17-8be23f6a1475.jpeg" alt="README.md" width="250" float="right">

# Jararaca Microservice Framework

## Overview

Jararaca is a aio-first microservice framework that provides a set of tools to build and deploy microservices in a simple and clear way.

## Features

### Hexagonal Architecture

The framework is based on the hexagonal architecture, which allows you to separate the business logic from the infrastructure, making the code more testable and maintainable.

### Dependency Injection

The framework uses the dependency injection pattern to manage the dependencies between the components of the application.

```py
    app = Microservice(
        providers=[
            ProviderSpec(
                provide=Token(AuthConfig, "AUTH_CONFIG"),
                use_value=AuthConfig(
                    secret="secret",
                    identity_refresh_token_expires_delta_seconds=60 * 60 * 24 * 30,
                    identity_token_expires_delta_seconds=60 * 60,
                ),
            ),
            ProviderSpec(
                provide=Token(AppConfig, "APP_CONFIG"),
                use_factory=AppConfig.provider,
            ),
            ProviderSpec(
                provide=TokenBlackListService,
                use_value=InMemoryTokenBlackListService(),
            ),
        ],
    )
```

### Web Server Port

The framework provides a web server that listens on a specific port and routes the requests to the appropriate handler. It uses [FastAPI](https://fastapi.tiangolo.com/) as the web framework.

```py
    @Delete("/{task_id}")
    async def delete_task(self, task_id: TaskId) -> None:
        await self.tasks_crud.delete_by_id(task_id)

        await use_ws_manager().broadcast(("Task %s deleted" % task_id).encode())
```

### Message Bus

The framework provides a topic-based message bus that allows you to send messages between the components of the application. It uses [AIO Pika](https://aio-pika.readthedocs.io/) as the message broker worker and publisher.

```py
    @IncomingHandler("task")
    async def process_task(self, message: Message[Identifiable[TaskSchema]]) -> None:
        name = generate_random_name()
        now = asyncio.get_event_loop().time()
        print("Processing task: ", name)

        task = message.payload()

        print("Received task: ", task)
        await asyncio.sleep(random.randint(1, 5))

        await use_publisher().publish(task, topic="task")

        then = asyncio.get_event_loop().time()
        print("Task Finished: ", name, " Time: ", then - now)
```

### Distributed Websocket

You can setup a room-based websocket server that allows you to send messages to a specific room or broadcast messages to all connected clients. All backend instances communicates with each other using a pub/sub mechanism (such as Redis).

```py
    @WebSocketEndpoint("/ws")
    async def ws_endpoint(self, websocket: WebSocket) -> None:
        await websocket.accept()
        counter.increment()
        await use_ws_manager().add_websocket(websocket)
        await use_ws_manager().join(["tasks"], websocket)
        await use_ws_manager().broadcast(
            ("New Connection (%d) from %s" % (counter.count, self.hostname)).encode()
        )

        print("New Connection (%d)" % counter.count)

        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                counter.decrement()
                await use_ws_manager().remove_websocket(websocket)

                await use_ws_manager().broadcast(
                    (
                        "Connection Closed (%d) from %s"
                        % (counter.count, self.hostname)
                    ).encode()
                )
                print("Connection Closed (%d)" % counter.count)
                break
```

### Scheduled Routine

You can setup a scheduled routine that runs a specific task at a specific time or interval.

```py
...
    @ScheduledAction("* * * * * */3", allow_overlap=False)
    async def scheduled_task(self) -> None:
        print("Scheduled Task at ", asyncio.get_event_loop().time())

        print("sleeping")
        await asyncio.sleep(5)

        await use_publisher().publish(
            message=Identifiable(
                id=uuid4(),
                data=TaskSchema(name=generate_random_name()),
            ),
            topic="task",
        )
```

## Installation

```bash
pip install jararaca
```

## Usage

### Create a Microservice

```python
# app.py

from jararaca import Microservice, create_http_server, create_messagebus_worker
from jararaca.presentation.http_microservice import HttpMicroservice

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


# App for specific Http Configuration Context
http_app = HttpMicroservice(app)

web_app = create_http_server(app)

```

### Run as a Web Server

```bash
uvicorn app:web_app --reload
# or
jararaca server app:app
# or
jararaca server app:http_app

```

### Run as a Message Bus Worker

```bash
jararaca worker app:app
```

### Run as a scheduled routine

```bash
jararaca scheduler app:app
```

### Generate Typescript intefaces from microservice app controllers

```bash
jararaca gen-tsi app.main:app app.ts
```

### Documentation

Documentation is under construction [here](https://luscasleo.github.io/jararaca/).
