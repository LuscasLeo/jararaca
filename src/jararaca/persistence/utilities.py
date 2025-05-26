import asyncio
import logging
from datetime import UTC, date, datetime
from functools import reduce
from typing import (
    Annotated,
    Any,
    Awaitable,
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
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import DateTime, Result, Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from jararaca.persistence.base import (
    IDENTIFIABLE_SCHEMA_T,
    T_BASEMODEL,
    BaseEntity,
    recursive_get_dict,
)
from jararaca.persistence.sort_filter import (
    FilterModel,
    FilterRuleApplier,
    SortModel,
    SortRuleApplier,
)

logger = logging.getLogger(__name__)


class Identifiable(BaseModel, Generic[IDENTIFIABLE_SCHEMA_T]):
    id: UUID
    data: IDENTIFIABLE_SCHEMA_T

    @staticmethod
    def instance(
        id: UUID, data: IDENTIFIABLE_SCHEMA_T
    ) -> "Identifiable[IDENTIFIABLE_SCHEMA_T]":
        return Identifiable[data.__class__](id=id, data=data)  # type: ignore[name-defined]


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

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    @classmethod
    def from_identifiable(cls, model: Identifiable[T_BASEMODEL]) -> "Self":
        """
        Converts an Identifiable model instance to an instance of the calling class.
        This method takes an instance of a class that implements the Identifiable interface
        and uses its `id` and `data` attributes to create a new instance of the calling class.
        Args:
            model (Identifiable[T_BASEMODEL]): An instance of a class that implements the Identifiable interface.
        Returns:
            Self: A new instance of the calling class with attributes populated from the model.
        Deprecated:
            This method is deprecated and will be removed in a future release.
        """

        return cls(**{"id": model.id, **model.data.model_dump()})

    def to_identifiable(self, MODEL: Type[T_BASEMODEL]) -> Identifiable[T_BASEMODEL]:
        """
        Converts the current instance to an Identifiable object of the specified model type.
        Args:
            MODEL (Type[T_BASEMODEL]): The model type to convert the current instance to.
        Returns:
            Identifiable[T_BASEMODEL]: An Identifiable object containing the id and data of the current instance.
        Raises:
            ValidationError: If the conversion fails due to validation errors.
        Note:
            This method is **deprecated** and may be removed in future versions.

        """
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


# region PaginatedFilter
class PaginatedFilter(BaseModel):
    page: Annotated[int, Field(gt=-1)] = 0
    page_size: Annotated[int, Field(gt=0)] = 10
    sort_models: list[SortModel] = []
    filter_models: list[FilterModel] = []


class QueryInjector(Protocol):

    def inject(self, query: Select[Tuple[Any]], filter: Any) -> Select[Tuple[Any]]: ...


# endregion


QUERY_ENTITY_T = TypeVar("QUERY_ENTITY_T", bound=BaseEntity)
QUERY_FILTER_T = TypeVar("QUERY_FILTER_T", bound=PaginatedFilter)


TRANSFORM_T = TypeVar("TRANSFORM_T")
PAGINATED_T = TypeVar("PAGINATED_T", bound=Any)


class Paginated(BaseModel, Generic[PAGINATED_T]):
    items: list[PAGINATED_T]
    total: int
    unpaginated_total: int
    total_pages: int

    def transform(
        self,
        transform: Callable[[PAGINATED_T], TRANSFORM_T],
    ) -> "Paginated[TRANSFORM_T]":
        return Paginated[TRANSFORM_T](
            items=[transform(item) for item in self.items],
            total=self.total,
            unpaginated_total=self.unpaginated_total,
            total_pages=self.total_pages,
        )

    async def transform_async(
        self,
        transform: Callable[[PAGINATED_T], Awaitable[TRANSFORM_T]],
        gather: bool = False,
    ) -> "Paginated[TRANSFORM_T]":
        """
        Transform the items of the paginated result asynchronously.

        Args:
            transform: The transformation function.
            gather: If the items should be gathered in a single async call.
            SQL Alchemy async session queries may cannot be gathered. Use this option with caution.
        """

        items = (
            await asyncio.gather(*[transform(item) for item in self.items])
            if gather
            else [await transform(item) for item in self.items]
        )
        return Paginated[TRANSFORM_T](
            items=items,
            total=self.total,
            unpaginated_total=self.unpaginated_total,
            total_pages=self.total_pages,
        )


class QueryOperations(Generic[QUERY_FILTER_T, QUERY_ENTITY_T]):

    def __init__(
        self,
        entity_type: Type[QUERY_ENTITY_T],
        session_provider: Callable[[], AsyncSession],
        filters_functions: list[QueryInjector],
        unique: bool = False,
        sort_rule_applier: SortRuleApplier | None = None,
        filter_rule_applier: FilterRuleApplier | None = None,
    ) -> None:
        self.entity_type: type[QUERY_ENTITY_T] = entity_type
        self.session_provider = session_provider
        self.filters_functions = filters_functions
        self.unique = unique
        self.sort_rule_applier = sort_rule_applier
        self.filter_rule_applier = filter_rule_applier

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
        """
        Executes a query with the provided filter and interceptors.
        Args:
            filter: The filter to apply to the query.
            interceptors: A list of functions that can modify the query before execution.
        Returns:
            Paginated[QUERY_ENTITY_T]: A paginated result containing the items and metadata.
        """

        tier_one_filtered_query = self.generate_filtered_query(
            filter, select(self.entity_type)
        )

        tier_two_filtered_query = reduce(
            lambda query, interceptor: interceptor(query),
            interceptors,
            tier_one_filtered_query,
        )

        if self.sort_rule_applier:
            tier_two_filtered_query = (
                self.sort_rule_applier.create_query_for_sorting_list(
                    tier_two_filtered_query, filter.sort_models
                )
            )

        if self.filter_rule_applier:
            tier_two_filtered_query = (
                self.filter_rule_applier.create_query_for_filter_list(
                    tier_two_filtered_query, filter.filter_models
                )
            )

        unpaginated_total = (
            await self.session.execute(
                select(func.count()).select_from(tier_two_filtered_query.subquery())
            )
        ).scalar_one()

        paginated_query = tier_two_filtered_query.limit(filter.page_size).offset(
            (filter.page) * filter.page_size
        )

        paginated_total = (
            await self.session.execute(
                select(func.count()).select_from(paginated_query.subquery())
            )
        ).scalar_one()

        result = await self.session.execute(paginated_query)
        result_scalars = list(self.judge_unique(result).scalars().all())

        return Paginated(
            items=result_scalars,
            total=paginated_total,
            unpaginated_total=unpaginated_total,
            total_pages=int(unpaginated_total / filter.page_size) + 1,
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
