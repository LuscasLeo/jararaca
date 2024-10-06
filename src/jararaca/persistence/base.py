import logging
from datetime import UTC, date, datetime
from functools import reduce
from typing import (
    Any,
    Callable,
    Generic,
    Iterable,
    Literal,
    Protocol,
    Self,
    Tuple,
    Type,
    TypeVar,
)
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import DateTime, Result, Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

IDENTIFIABLE_SCHEMA_T = TypeVar("IDENTIFIABLE_SCHEMA_T")
logger = logging.getLogger(__name__)


class Identifiable(BaseModel, Generic[IDENTIFIABLE_SCHEMA_T]):
    id: UUID
    data: IDENTIFIABLE_SCHEMA_T


T_BASEMODEL = TypeVar("T_BASEMODEL", bound=BaseModel)


def recursive_get_dict(obj: Any) -> Any:
    if isinstance(obj, list):
        return [recursive_get_dict(v) for v in obj]
    elif hasattr(obj, "__dict__"):
        return {
            k: recursive_get_dict(v) for k, v in obj.__dict__.items() if k[0] != "_"
        }
    else:
        return obj


class BaseEntity(DeclarativeBase):

    @classmethod
    def from_basemodel(cls, mutation: T_BASEMODEL) -> "Self":
        intersection = set(cls.__annotations__.keys()) & set(
            mutation.model_fields.keys()
        )
        return cls(**{k: getattr(mutation, k) for k in intersection})

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
        try:
            return Identifiable[MODEL].model_validate(  # type: ignore[valid-type]
                {"id": self.id, "data": recursive_get_dict(self)}
            )
        except ValidationError:
            logger.critical(
                "Error on to_identifiable for identifiable id %s of class %s table '%s'",
                self.id,
                self.__class__,
                self.__tablename__,
            )
            raise


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

    async def get_many(self, ids: Iterable[UUID]) -> Iterable[IDENTIFIABLE_T]:
        return await self.session.scalars(
            select(self.entity_type).where(self.entity_type.id.in_(ids))
        )

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

    async def exists(self, id: UUID) -> bool:
        return (
            await self.session.execute(
                select(
                    select(self.entity_type).where(self.entity_type.id == id).exists()
                )
            )
        ).scalar_one()

    async def exists_some(self, ids: Iterable[UUID]) -> bool:
        return (
            await self.session.execute(
                select(
                    select(self.entity_type)
                    .where(self.entity_type.id.in_(ids))
                    .exists()
                )
            )
        ).scalar_one()

    async def exists_all(self, ids: set[UUID]) -> bool:

        return (
            await self.session.execute(
                select(func.count())
                .select_from(self.entity_type)
                .where(self.entity_type.id.in_(ids))
            )
        ).scalar_one() >= len(ids)

    async def intersects(self, ids: set[UUID]) -> set[UUID]:
        return set(
            (
                await self.session.execute(
                    select(self.entity_type.id).where(self.entity_type.id.in_(ids))
                )
            ).scalars()
        )

    async def difference(self, ids: set[UUID]) -> set[UUID]:
        return ids - set(
            (
                await self.session.execute(
                    select(self.entity_type.id).where(self.entity_type.id.in_(ids))
                )
            ).scalars()
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
        unique: bool = False,
    ) -> None:
        self.entity_type: type[QUERY_ENTITY_T] = entity_type
        self.session_provider = session_provider
        self.filters_functions = filters_functions
        self.unique = unique

    @property
    def session(self) -> AsyncSession:
        return self.session_provider()

    async def query(
        self,
        filter: QUERY_FILTER_T,
        interceptors: list[
            Callable[[Select[Tuple[QUERY_ENTITY_T]]], Select[Tuple[QUERY_ENTITY_T]]]
        ] = [],
    ) -> "Paginated[QUERY_ENTITY_T]":
        unfiltered_total = (
            await self.session.execute(
                select(func.count()).select_from(self.entity_type)
            )
        ).scalar_one()

        query = reduce(
            lambda query, interceptor: interceptor(query),
            interceptors,
            select(self.entity_type),
        )

        filtered_query = self.generate_filtered_query(filter, query)

        filtered_total = (
            await self.session.execute(
                select(func.count()).select_from(filtered_query.subquery())
            )
        ).scalar_one()

        return Paginated(
            items=[
                e
                for e in self.judge_unique(
                    await self.session.execute(
                        self.generate_filtered_query(filter, filtered_query)
                    )
                ).scalars()
            ],
            total=filtered_total,
            unfiltered_total=unfiltered_total,
        )

    def judge_unique(
        self, result: Result[Tuple[QUERY_ENTITY_T]]
    ) -> Result[Tuple[QUERY_ENTITY_T]]:
        if self.unique:
            return result.unique()
        return result

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
