from datetime import UTC, date, datetime
from functools import reduce
from typing import Any, Callable, Generic, Literal, Protocol, Self, Tuple, Type, TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import DateTime, Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

IDENTIFIABLE_SCHEMA_T = TypeVar("IDENTIFIABLE_SCHEMA_T")


class Identifiable(BaseModel, Generic[IDENTIFIABLE_SCHEMA_T]):
    id: UUID
    data: IDENTIFIABLE_SCHEMA_T


T_BASEMODEL = TypeVar("T_BASEMODEL", bound=BaseModel)


def recursive_get_dict(obj: Any) -> Any:
    if hasattr(obj, "__dict__"):
        return {
            k: recursive_get_dict(v) for k, v in obj.__dict__.items() if k[0] != "_"
        }
    elif isinstance(obj, list):
        return [recursive_get_dict(v) for v in obj]
    else:
        return obj


class BaseEntity(DeclarativeBase):

    @classmethod
    def from_basemodel(cls, mutation: T_BASEMODEL) -> "Self":
        return cls(**mutation.model_dump())

    def to_basemodel(self, model: Type[T_BASEMODEL]) -> T_BASEMODEL:
        return model.model_validate(recursive_get_dict(self))


def nowutc() -> datetime:
    return datetime.now(UTC)


class DatedEntity(BaseEntity):
    __abstract__ = True

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=nowutc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=nowutc
    )


class IdentifiableEntity(BaseEntity):
    __abstract__ = True

    id: Mapped[UUID] = mapped_column(primary_key=True)

    @classmethod
    def from_identifiable(cls, model: Identifiable[T_BASEMODEL]) -> "Self":
        return cls(**{"id": model.id, **model.data.model_dump()})

    def to_identifiable(self, MODEL: Type[T_BASEMODEL]) -> Identifiable[T_BASEMODEL]:

        return Identifiable[MODEL].model_validate(  # type: ignore[valid-type]
            {"id": self.id, "data": recursive_get_dict(self)}
        )


IDENTIFIABLE_T = TypeVar("IDENTIFIABLE_T", bound=IdentifiableEntity)


class CRUDOperations(Generic[IDENTIFIABLE_T]):

    def __init__(
        self,
        entity_type: Type[IDENTIFIABLE_T],
        session_provider: Callable[[], AsyncSession],
    ) -> None:
        self.entity_type = entity_type
        self.session_provider = session_provider

    @property
    def session(self) -> AsyncSession:
        return self.session_provider()

    async def create(self, entity: IDENTIFIABLE_T) -> None:
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)

    async def get(self, id: UUID) -> IDENTIFIABLE_T:
        return await self.session.get_one(self.entity_type, id)

    async def update(self, entity: IDENTIFIABLE_T) -> None:
        await self.session.merge(entity)

    async def delete(self, entity: IDENTIFIABLE_T) -> None:
        await self.session.delete(entity)

    async def delete_by_id(self, id: UUID) -> None:
        await self.session.execute(
            delete(self.entity_type).where(self.entity_type.id == id)
        )

    async def update_by_id(self, id: UUID, entity: IDENTIFIABLE_T) -> None:
        await self.session.execute(
            update(self.entity_type)
            .where(self.entity_type.id == id)
            .values(entity.__dict__)
        )


QUERY_ENTITY_T = TypeVar("QUERY_ENTITY_T", bound=BaseEntity)
QUERY_FILTER_T = TypeVar("QUERY_FILTER_T")


class QueryInjector(Protocol):

    def inject(self, query: Select[Tuple[Any]], filter: Any) -> Select[Tuple[Any]]: ...


TRANSFORM_T = TypeVar("TRANSFORM_T")
PAGINATED_T = TypeVar("PAGINATED_T", bound=Any)


class Paginated(BaseModel, Generic[PAGINATED_T]):
    items: list[PAGINATED_T]
    total: int
    unfiltered_total: int

    def transform(
        self,
        transform: Callable[[PAGINATED_T], TRANSFORM_T],
    ) -> "Paginated[TRANSFORM_T]":
        return Paginated[TRANSFORM_T](
            items=[transform(item) for item in self.items],
            total=self.total,
            unfiltered_total=self.unfiltered_total,
        )


class QueryOperations(Generic[QUERY_FILTER_T, QUERY_ENTITY_T]):

    def __init__(
        self,
        entity_type: Type[QUERY_ENTITY_T],
        session_provider: Callable[[], AsyncSession],
        filters_functions: list[QueryInjector],
    ) -> None:
        self.entity_type: type[QUERY_ENTITY_T] = entity_type
        self.session_provider = session_provider
        self.filters_functions = filters_functions

    @property
    def session(self) -> AsyncSession:
        return self.session_provider()

    async def query(self, filter: QUERY_FILTER_T) -> "Paginated[QUERY_ENTITY_T]":
        unfiltered_total = (
            await self.session.execute(
                select(func.count()).select_from(self.entity_type)
            )
        ).scalar_one()

        filtered_query = self.generate_filtered_query(filter, select(self.entity_type))

        filtered_total = (
            await self.session.execute(
                select(func.count()).select_from(filtered_query.subquery())
            )
        ).scalar_one()

        return Paginated(
            items=[
                e
                for e in (
                    await self.session.execute(
                        self.generate_filtered_query(filter, select(self.entity_type))
                    )
                ).scalars()
            ],
            total=filtered_total,
            unfiltered_total=unfiltered_total,
        )

    def generate_filtered_query(
        self, filter: QUERY_FILTER_T, select_query: Select[Tuple[QUERY_ENTITY_T]]
    ) -> Select[Tuple[QUERY_ENTITY_T]]:
        return reduce(
            lambda query, filter_function: filter_function.inject(query, filter),
            self.filters_functions,
            select_query,
        )


# region PaginatedFilter
class PaginatedFilter(BaseModel):
    offset: int = 0
    limit: int = 10


class PaginatedQueryInjector(QueryInjector):
    def inject(
        self,
        query: Select[Tuple[BaseEntity]],
        filter: PaginatedFilter,
    ) -> Select[Tuple[BaseEntity]]:
        return query.offset(filter.offset).limit(filter.limit)


# endregion

# DateOrderedFilter


class DateOrderedFilter(BaseModel):
    order_by: Literal["asc", "desc"] = "asc"


class DateOrderedQueryInjector(QueryInjector):

    def __init__(self, entity_type: Type[DatedEntity]) -> None:
        self.entity_type = entity_type

    def inject(
        self,
        query: Select[Tuple[DatedEntity]],
        filter: DateOrderedFilter,
    ) -> Select[Tuple[DatedEntity]]:
        return query.order_by(getattr(self.entity_type.created_at, filter.order_by)())


# region Criteria


# region Criteria


class StringCriteria(BaseModel):
    value: str
    is_exact: bool
    case_sensitive: bool


class DateCriteria(BaseModel):
    value: date
    op: Literal["eq", "gt", "lt", "gte", "lte"]


class DatetimeCriteria(BaseModel):
    value: datetime
    op: Literal["eq", "gt", "lt", "gte", "lte"]


class CriteriaBasedAttributeQueryInjector(QueryInjector):

    def __init__(self, entity_type: Type[BaseEntity]) -> None:
        self.entity_type = entity_type

    def inject(
        self, query: Select[Tuple[BaseEntity]], filter: Any
    ) -> Select[Tuple[BaseEntity]]:

        attrs = filter.__dict__

        for field_name, value in attrs.items():

            if isinstance(value, (DateCriteria, DatetimeCriteria)):
                value = getattr(filter, field_name)

                entity_field = getattr(self.entity_type, field_name)

                op_mapping = {
                    "eq": entity_field == value.value,
                    "gt": entity_field > value.value,
                    "lt": entity_field < value.value,
                    "gte": entity_field >= value.value,
                    "lte": entity_field <= value.value,
                }

                query = query.filter(op_mapping[value.op])
            elif isinstance(value, StringCriteria):
                value = getattr(filter, field_name)

                entity_field = getattr(self.entity_type, field_name)

                if value.is_exact:
                    query = query.filter(entity_field == value.value)
                else:
                    query = query.filter(entity_field.contains(value.value))

        return query


# endregion
