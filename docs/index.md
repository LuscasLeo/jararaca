# Jararaca Microservice


## Overview

Jararaca is a python microservice that provides all resources for a Microservice Architecture.


## Installation

```bash
pip install jararaca
```

## Usage

```py
# src/app/main.py
from app.app_config import AppConfig, AppFactoryWithAppConfig
from app.auth.auth_controller import (
    AuthConfig,
    AuthController,
    InMemoryTokenBlackListService,
    TokenBlackListService,
)
from app.extraction.models_controller import ExtractionModelController
from app.extraction.secrets_controller import SecretsController
from app.extraction.tasks_controller import TasksController
from app.providers import REDIS_TOKEN
from redis.asyncio import Redis

from jararaca import (
    AIOPikaConnectionFactory,
    AIOSQAConfig,
    AIOSqlAlchemySessionInterceptor,
    AppConfigurationInterceptor,
    HttpMicroservice,
    MessageBusPublisherInterceptor,
    Microservice,
    ProviderSpec,
    RedisWebSocketConnectionBackend,
    Token,
    WebSocketInterceptor,
    create_http_server,
)

app = Microservice(
    providers=[
        ProviderSpec(
            provide=REDIS_TOKEN,
            use_factory=AppFactoryWithAppConfig(
                lambda config: Redis.from_url(config.REDIS_URL, decode_responses=False)
            ),
            after_interceptors=True,
        ),
        ProviderSpec(
            provide=Token(AuthConfig, "AUTH_CONFIG"),
            use_value=AuthConfig(
                secret="secret",
                identity_refresh_token_expires_delta_seconds=60 * 60 * 24 * 30,
                identity_token_expires_delta_seconds=60 * 60,
            ),
        ),
        ProviderSpec(
            provide=TokenBlackListService,
            use_value=InMemoryTokenBlackListService(),
        ),
    ],
    controllers=[
        TasksController,
    ],
    interceptors=[
        AppConfigurationInterceptor(
            global_configs=[
                (Token(AppConfig, "APP_CONFIG"), AppConfig),
            ]
        ),
        AppFactoryWithAppConfig(
            lambda config: MessageBusPublisherInterceptor(
                connection_factory=AIOPikaConnectionFactory(
                    url=config.AMQP_URL,
                    exchange="jararaca_ex",
                ),
            )
        ),
        AppFactoryWithAppConfig(
            lambda config: AIOSqlAlchemySessionInterceptor(
                AIOSQAConfig(
                    connection_name="default",
                    url=config.DATABASE_URL,
                )
            )
        ),
        AppFactoryWithAppConfig(
            lambda config: WebSocketInterceptor(
                backend=RedisWebSocketConnectionBackend(
                    send_pubsub_channel="jararaca:websocket:send",
                    broadcast_pubsub_channel="jararaca:websocket:broadcast",
                    conn=Redis.from_url(config.REDIS_URL, decode_responses=False),
                )
            ),
        ),
    ],
)


# FastAPI factory for uvicorn
http_app = create_http_server(
    HttpMicroservice(
        app=app,
        factory=fastapi_factory,
    )
)
```


```py
# src/app/app_config.py
from typing import Annotated, Any, Callable

from pydantic import BaseModel

from jararaca import Token


class AppConfig(BaseModel):

    DATABASE_URL: str
    REDIS_URL: str
    AMQP_URL: str


APP_CONFIG_TOKEN = Token(AppConfig, "APP_CONFIG")


class AppFactoryWithAppConfig:

    def __init__(self, callback: Callable[[AppConfig], Any]):
        self.callback = callback

    def __call__(self, app_config: Annotated[AppConfig, APP_CONFIG_TOKEN]) -> Any:
        return self.callback(app_config)

```


```py
# src/app/providers.py

from typing import cast

from redis.asyncio import Redis

from jararaca import Token

REDIS_TOKEN = Token["Redis[bytes]"](cast("type[Redis[bytes]]", Redis), "REDIS_AWS")
```

```py
# src/app/extraction/tasks/tasks_controller.py

import asyncio
import random
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from app.app_config import AppConfig
from app.extraction.entity import ExtractionModelEntity, SecretEntity, TaskEntity
from app.extraction.schemas import ExtractionModelSchema
from app.providers import REDIS_TOKEN
from fastapi import Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, field_validator
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.exc import NoResultFound

from jararaca import (
    CriteriaBasedAttributeQueryInjector,
    CRUDOperations,
    DateCriteria,
    DateOrderedFilter,
    DateOrderedQueryInjector,
    Get,
    Identifiable,
    IncomingHandler,
    Message,
    MessageBusController,
    Paginated,
    PaginatedFilter,
    PaginatedQueryInjector,
    Post,
    Put,
    QueryOperations,
    RestController,
    ScheduledAction,
    StringCriteria,
    Token,
    WebSocketEndpoint,
    use_publisher,
    use_session,
    use_ws_manager,
)

TaskId = UUID


class TaskReportSchema(BaseModel):
    report: str
    status: Literal["SUCCESS", "ERROR"]


class TaskSchema(BaseModel):
    status: Literal["PENDING", "RUNNING", "FINISHED", "ERROR"]
    extraction_model_schema: ExtractionModelSchema
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator("extraction_model_schema", mode="before")
    @classmethod
    def extraction_model_validator(cls, value: Any) -> ExtractionModelSchema:
        if isinstance(value, dict):
            return ExtractionModelSchema.model_validate(value)
        elif isinstance(value, str):
            return ExtractionModelSchema.model_validate_json(value)
        elif isinstance(value, ExtractionModelSchema):
            return value
        else:
            raise ValueError("Invalid Extraction Model Schema")


class CreateTaskSchema(BaseModel):
    extraction_model_id: UUID


class TaskSimpleFilter(PaginatedFilter, DateOrderedFilter): ...


class DatedFilter(BaseModel):
    created_at: DateCriteria | None = None
    updated_at: DateCriteria | None = None


class TaskComplexFilter(PaginatedFilter, DatedFilter):
    name: StringCriteria | None = None


@MessageBusController()
@RestController("/tasks")
class TasksController:

    def __init__(
        self,
        app_config: Annotated[AppConfig, Token(AppConfig, "APP_CONFIG")],
        redis: Annotated["Redis[bytes]", REDIS_TOKEN],
    ) -> None:
        self.app_config = app_config
        self.redis = redis
        self.tasks_crud = CRUDOperations(TaskEntity, use_session)
        self.tasks_simple_query_operations = QueryOperations[
            TaskSimpleFilter, TaskEntity
        ](
            TaskEntity,
            use_session,
            [
                PaginatedQueryInjector(),
                DateOrderedQueryInjector(TaskEntity),
            ],
        )

        self.tasks_complex_query_operations = QueryOperations[
            TaskComplexFilter, TaskEntity
        ](
            TaskEntity,
            use_session,
            [
                PaginatedQueryInjector(),
                CriteriaBasedAttributeQueryInjector(TaskEntity),
                DateOrderedQueryInjector(TaskEntity),
            ],
        )

        self.extraction_model_crud = CRUDOperations(ExtractionModelEntity, use_session)

    @Post("/")
    async def create_task(self, task: CreateTaskSchema) -> Identifiable[TaskSchema]:
        try:
            extraction_model = await self.extraction_model_crud.get(
                task.extraction_model_id
            )
        except NoResultFound:
            raise HTTPException(status_code=404, detail="Extraction Model not found")

        task_entity = TaskEntity.from_basemodel(task)
        task_entity.id = uuid4()

        task_entity.extraction_model_schema = (
            extraction_model.to_model().model_dump_json()
        )

        await self.tasks_crud.create(task_entity)
        await use_ws_manager().broadcast(b"New Task Created")
        await use_publisher().publish(
            task_entity.to_identifiable(TaskSchema), topic="task"
        )

        return task_entity.to_identifiable(TaskSchema)

    @Post("/{task_id}")
    async def get_task(self, task_id: TaskId) -> Identifiable[TaskSchema]:
        task = await self.tasks_crud.get(task_id)
        return task.to_identifiable(TaskSchema)

    @Get("/")
    async def simple_query_tasks(
        self, filter: Annotated[TaskSimpleFilter, Depends(TaskSimpleFilter)]
    ) -> Paginated[Identifiable[TaskSchema]]:
        pagination = await self.tasks_simple_query_operations.query(filter)
        return pagination.transform(lambda task: task.to_identifiable(TaskSchema))

    @Post("/query")
    async def complex_query_tasks(
        self, filter: TaskComplexFilter
    ) -> Paginated[Identifiable[TaskSchema]]:
        pagination = await self.tasks_complex_query_operations.query(filter)
        return pagination.transform(lambda task: task.to_identifiable(TaskSchema))

    @Put("/{task_id}")
    async def update_task(self, task_id: TaskId, task: TaskSchema) -> None:
        task_entity = TaskEntity.from_basemodel(task)
        await self.tasks_crud.update_by_id(task_id, task_entity)

    @IncomingHandler("task")
    async def process_task(self, message: Message[Identifiable[TaskSchema]]) -> None:
        payload = message.payload()

        current_status = (
            await use_session().execute(
                select(TaskEntity.status).filter(TaskEntity.id == payload.id)
            )
        ).scalar()

        if current_status != "PENDING":
            print("Task %s is not pending, is %s" % (payload.id, current_status))
            return

        async with use_session().begin_nested():
            await use_session().execute(
                update(TaskEntity)
                .filter(TaskEntity.id == payload.id)
                .values(status="RUNNING", started_at=datetime.now())
            )
            print("Processing Task: ", payload.id)
            await use_session().commit()

            await use_ws_manager().broadcast(
                ("Task %s is running" % payload.id).encode()
            )

        print("Processing")
        await asyncio.sleep(3)
        async with use_session().begin_nested():

            await use_session().commit()

            await use_ws_manager().broadcast(
                ("Task %s is finished" % payload.id).encode()
            )

    @ScheduledAction("* * * * * */5")
    async def scheduled_task2(self) -> None:
        print("Scheduled Task 2")
        pending_tasks = (
            (
                await use_session().execute(
                    select(TaskEntity).filter(TaskEntity.status == "PENDING")
                )
            )
            .scalars()
            .all()
        )

        for task in pending_tasks:
            if not await self.redis.sismember("tasks", str(task.id)):

                async with self.redis.pipeline() as pipe:
                    try:

                        await pipe.sadd("tasks", str(task.id))
                        await use_publisher().publish(
                            task.to_identifiable(TaskSchema), topic="task"
                        )
                        await pipe.expire("tasks", timedelta(minutes=5))
                        await pipe.execute()
                    except Exception as e:
                        print("Error: ", e)
                        await pipe.reset()
                        raise e

    @WebSocketEndpoint("/ws")
    async def ws_endpoint(self, websocket: WebSocket) -> None:
        await websocket.accept()
        counter.increment()
        await use_ws_manager().add_websocket(websocket)
        await use_ws_manager().join(["tasks"], websocket)

        print("New Connection (%d)" % counter.count)

        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                counter.decrement()
                await use_ws_manager().remove_websocket(websocket)

                print("Connection Closed (%d)" % counter.count)
                break

```


```py
# src/app/extraction/entity.py

import json
from datetime import datetime
from typing import List, Literal
from uuid import UUID

from app.extraction.schemas import ExtractionModelSchema, KeyValue
from pydantic import RootModel
from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jararaca import T_BASEMODEL, DatedEntity, Identifiable, IdentifiableEntity


class TaskEntity(IdentifiableEntity, DatedEntity):

    __tablename__ = "tasks"

    extraction_model_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_model_schema: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[Literal["PENDING", "RUNNING", "FINISHED", "ERROR"]] = mapped_column(
        nullable=False, default="PENDING"
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    report: Mapped["TaskReportEntity | None"] = relationship(
        "TaskReportEntity",
        uselist=False,
        backref="task",
        cascade="all, delete-orphan",
        lazy="joined",
    )


class TaskReportEntity(DatedEntity):

    __tablename__ = "task_reports"

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id"), nullable=False, primary_key=True
    )
    report: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[Literal["SUCCESS", "ERROR"]] = mapped_column(nullable=False)


class SecretEntity(IdentifiableEntity, DatedEntity):

    __tablename__ = "secrets"

    name: Mapped[str] = mapped_column(nullable=False, unique=True)
    value: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str] = mapped_column(nullable=False)


class ExtractionModelEntity(IdentifiableEntity, DatedEntity):

    __tablename__ = "extraction_models"

    url: Mapped[str] = mapped_column(nullable=False)
    method: Mapped[str] = mapped_column(nullable=False)
    headers_json: Mapped[str] = mapped_column(nullable=False)
    params_json: Mapped[str] = mapped_column(nullable=False)
    query_json: Mapped[str] = mapped_column(nullable=False)

    @classmethod
    def from_model(cls, model: ExtractionModelSchema) -> "ExtractionModelEntity":
        return cls(
            url=model.url,
            method=model.method,
            headers_json=json.dumps([e.model_dump() for e in model.headers]),
            params_json=json.dumps([e.model_dump() for e in model.params]),
            query_json=json.dumps([e.model_dump() for e in model.query]),
        )

    def to_model(self) -> ExtractionModelSchema:
        return ExtractionModelSchema(
            url=self.url,
            method=self.method,
            headers=RootModel[List[KeyValue]]
            .model_validate_json(self.headers_json)
            .root,
            params=RootModel[List[KeyValue]].model_validate_json(self.params_json).root,
            query=RootModel[List[KeyValue]].model_validate_json(self.query_json).root,
        )

    def to_identifiable(self, MODEL: type[T_BASEMODEL]) -> Identifiable[T_BASEMODEL]:
        return Identifiable[MODEL].model_validate(  # type: ignore[valid-type]
            {"id": self.id, "data": self.to_model()}
        )

```
