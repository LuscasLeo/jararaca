from typing import Annotated, Any, Generic, Protocol, Type, TypeVar
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from jararaca.persistence.base import (
    CRUDOperations,
    Identifiable,
    IdentifiableEntity,
    QueryOperations,
)
from jararaca.persistence.interceptors.aiosqa_interceptor import use_session

ENTITY_T = TypeVar("ENTITY_T", bound=IdentifiableEntity)
SCHEMA_T = TypeVar("SCHEMA_T", bound=BaseModel)


SIMPLE_FILTER_TCO = TypeVar("SIMPLE_FILTER_TCO", contravariant=True)
COMPLEX_FILTER_TCO = TypeVar("COMPLEX_FILTER_TCO", contravariant=True)


class IGenericRouter(
    Protocol, Generic[SCHEMA_T, SIMPLE_FILTER_TCO, COMPLEX_FILTER_TCO]
):
    async def create_task(self, task: SCHEMA_T) -> Identifiable[SCHEMA_T]: ...

    async def get_task(self, task_id: UUID) -> Identifiable[SCHEMA_T]: ...

    async def simple_query_tasks(
        self, filter: Annotated[SIMPLE_FILTER_TCO, Depends(SIMPLE_FILTER_TCO)]
    ) -> list[Identifiable[SCHEMA_T]]: ...

    async def complex_query_tasks(
        self, filter: COMPLEX_FILTER_TCO
    ) -> list[Identifiable[SCHEMA_T]]: ...

    async def update_task(self, task: SCHEMA_T) -> None: ...

    async def delete_task(self, task_id: UUID) -> None: ...

    def get_router(self) -> APIRouter: ...


SIMPLE_FILTER_T = TypeVar("SIMPLE_FILTER_T")
COMPLEX_FILTER_T = TypeVar("COMPLEX_FILTER_T")


# class GenericRouter(Generic[ENTITY_T, SCHEMA_T, SIMPLE_FILTER_T, COMPLEX_FILTER_T]):


def create_generic_router_class(
    entity_type: Type[ENTITY_T],
    schema_type: Type[SCHEMA_T],
    simple_filter_type: Type[SIMPLE_FILTER_T],
    complex_filter_type: Type[COMPLEX_FILTER_T],
    simpel_filter: QueryOperations[SIMPLE_FILTER_T, ENTITY_T],
    complex_filter: QueryOperations[COMPLEX_FILTER_T, ENTITY_T],
) -> Type[IGenericRouter[SCHEMA_T, SIMPLE_FILTER_T, COMPLEX_FILTER_T]]:

    simple_query_operations = simpel_filter
    complex_query_operations = complex_filter
    crud = CRUDOperations[ENTITY_T](entity_type, use_session)

    async def create_task(self: Any, task: schema_type) -> Identifiable[schema_type]:  # type: ignore[valid-type]
        entity = entity_type.from_basemodel(task)
        entity.id = uuid4()
        await crud.create(entity)
        return entity.to_identifiable(schema_type)

    async def get_task(self: Any, task_id: UUID) -> Identifiable[schema_type]:  # type: ignore[valid-type]
        entity = await crud.get(task_id)
        return entity.to_identifiable(schema_type)

    async def simple_query_tasks(
        self: Any, filter: Annotated[SIMPLE_FILTER_T, Depends(simple_filter_type)]
    ) -> list[Identifiable[schema_type]]:  # type: ignore[valid-type]
        entities = await simple_query_operations.query(filter)
        return [task.to_identifiable(schema_type) for task in entities]

    async def complex_query_tasks(
        self: Any, filter: complex_filter_type  # type: ignore[valid-type]
    ) -> list[Identifiable[schema_type]]:  # type: ignore[valid-type]
        entities = await complex_query_operations.query(filter)
        return [task.to_identifiable(schema_type) for task in entities]

    async def update_task(self: Any, task: schema_type) -> None:  # type: ignore[valid-type]
        entity = entity_type.from_basemodel(task)
        await crud.update_by_id(entity.id, entity)

    async def delete_task(self: Any, task_id: UUID) -> None:
        await crud.delete_by_id(task_id)

    def get_router(self: Any) -> APIRouter:
        router = APIRouter(
            tags=[entity_type.__name__],
        )
        router.add_api_route(
            methods=["POST"],
            path="/",
            endpoint=create_task,
        )

        router.add_api_route(
            methods=["GET"],
            path="/{task_id}",
            endpoint=get_task,
        )

        router.add_api_route(
            methods=["GET"],
            path="/",
            endpoint=simple_query_tasks,
        )

        router.add_api_route(
            methods=["POST"],
            path="/query",
            endpoint=complex_query_tasks,
        )

        router.add_api_route(
            methods=["PUT"],
            path="/",
            endpoint=update_task,
        )

        router.add_api_route(
            methods=["DELETE"],
            path="/",
            endpoint=delete_task,
        )

        return router

    return type(
        f"{entity_type.__name__}Router",
        (),
        {
            "entity_type": entity_type,
            "schema_type": schema_type,
            "crud": CRUDOperations[ENTITY_T](entity_type, use_session),
            "simple_query_operations": simpel_filter,
            "complex_query_operations": complex_filter,
            "create_task": create_task,
            "get_task": get_task,
            "simple_query_tasks": simple_query_tasks,
            "complex_query_tasks": complex_query_tasks,
            "update_task": update_task,
            "delete_task": delete_task,
            "get_router": get_router,
        },
    )
