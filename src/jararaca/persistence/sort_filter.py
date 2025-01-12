from datetime import date, datetime
from functools import reduce
from typing import Literal, Tuple, TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Select
from sqlalchemy.orm.attributes import InstrumentedAttribute

from jararaca import BaseEntity

FILTER_SORT_ENTITY_ATTR_MAP = dict[
    str, InstrumentedAttribute[str | int | datetime | date | UUID]
]


STRING_OPERATORS = Literal[
    "contains",
    "doesNotContain",
    "equals",
    "doesNotEqual",
    "startsWith",
    "endsWith",
    "isEmpty",
    "isNotEmpty",
    "isAnyOf",
]


DATE_DATETIME_OPERATORS = Literal[
    "is", "not", "after", "onOrAfter", "before", "onOrBefore", "isEmpty", "isNotEmpty"
]

BOOLEAN_OPERATORS = Literal["is"]

NUMBER_OPERATORS = Literal[
    "=", "!=", ">", "<", ">=", "<=", "isEmpty", "isNotEmpty", "isAnyOf"
]


class SortModel(BaseModel):
    field: str
    direction: Literal["asc", "desc"]


class FilterModel(BaseModel):
    field: str
    operator: Literal[
        STRING_OPERATORS, DATE_DATETIME_OPERATORS, BOOLEAN_OPERATORS, NUMBER_OPERATORS
    ]
    value: str | list[str] = ""


INHERITS_BASE_ENTITY = TypeVar("INHERITS_BASE_ENTITY", bound=BaseEntity)


class SortFilterRunner:
    def __init__(
        self,
        allowed_filters: FILTER_SORT_ENTITY_ATTR_MAP,
        allowed_sorts: FILTER_SORT_ENTITY_ATTR_MAP,
    ):
        self.allowed_filters = allowed_filters
        self.allowed_sorts = allowed_sorts

    def create_query_for_filter(
        self, query: Select[Tuple[INHERITS_BASE_ENTITY]], filter: FilterModel
    ) -> Select[Tuple[INHERITS_BASE_ENTITY]]:
        field = self.allowed_filters.get(filter.field)
        if field is None:
            raise ValueError(f"Unsupported field: {filter.field}")
        field_type = field.property.columns[0].type.python_type

        if field_type is str:
            match filter.operator:
                case "contains":
                    return query.filter(field.contains(filter.value))
                case "doesNotContain":
                    return query.filter(~field.contains(filter.value))
                case "equals":
                    return query.filter(field == filter.value)
                case "doesNotEqual":
                    return query.filter(field != filter.value)
                case "startsWith":
                    return query.filter(field.startswith(filter.value))
                case "endsWith":
                    return query.filter(field.endswith(filter.value))
                case "isEmpty":
                    return query.filter(field == "")
                case "isNotEmpty":
                    return query.filter(field != "")
                case "isAnyOf":
                    return query.filter(field.in_(filter.value))
                case _:
                    raise ValueError(f"Unsupported string operator: {filter.operator}")
        elif field_type in [date, datetime]:
            __value = (
                filter.value[0] if isinstance(filter.value, list) else filter.value
            )
            if field_type is date:
                value = datetime.strptime(__value, "%Y-%m-%d").date()
            else:
                value = datetime.strptime(__value, "%Y-%m-%dT%H:%M:%S.%fZ")
            match filter.operator:
                case "is":
                    return query.filter(field == value)
                case "not":
                    return query.filter(field != value)
                case "after":
                    return query.filter(field > value)
                case "onOrAfter":
                    return query.filter(field >= value)
                case "before":
                    return query.filter(field < value)
                case "onOrBefore":
                    return query.filter(field <= value)
                case "isEmpty":
                    return query.filter(field == None)  # noqa
                case "isNotEmpty":
                    return query.filter(field != None)  # noqa
                case _:
                    raise ValueError(
                        f"Unsupported data/datetime operator: {filter.operator}"
                    )
        elif field_type is bool:
            __value = (
                filter.value[0] if isinstance(filter.value, list) else filter.value
            )
            match filter.operator:
                case "is":
                    if __value == "":
                        return query.filter(field.is_not(None))
                    return query.filter(field == (__value == "true"))
                case _:
                    raise ValueError(f"Unsupported bool operator: {filter.operator}")
        elif field_type is int:
            match filter.operator:
                case "=":
                    return query.filter(field == filter.value)
                case "!=":
                    return query.filter(field != filter.value)
                case ">":
                    return query.filter(field > filter.value)
                case "<":
                    return query.filter(field < filter.value)
                case ">=":
                    return query.filter(field >= filter.value)
                case "<=":
                    return query.filter(field <= filter.value)
                case "isEmpty":
                    return query.filter(field == None)  # noqa
                case "isNotEmpty":
                    return query.filter(field != None)  # noqa
                case "isAnyOf":
                    return query.filter(field.in_(filter.value))
                case _:
                    raise ValueError(f"Unsupported int operator: {filter.operator}")

        raise ValueError(f"Unsupported field type: {field_type}")

    def create_query_for_filter_list(
        self, query: Select[Tuple[INHERITS_BASE_ENTITY]], filters: list[FilterModel]
    ) -> Select[Tuple[INHERITS_BASE_ENTITY]]:
        return reduce(lambda q, f: self.create_query_for_filter(q, f), filters, query)

    def create_query_for_sorting(
        self, query: Select[Tuple[INHERITS_BASE_ENTITY]], sort: SortModel
    ) -> Select[Tuple[INHERITS_BASE_ENTITY]]:
        field = self.allowed_sorts.get(sort.field)
        if field is None:
            raise ValueError(f"Unsupported field: {sort.field}")
        return query.order_by(field.asc() if sort.direction == "asc" else field.desc())

    def create_query_for_sorting_list(
        self, query: Select[Tuple[INHERITS_BASE_ENTITY]], sorts: list[SortModel]
    ) -> Select[Tuple[INHERITS_BASE_ENTITY]]:
        return reduce(lambda q, s: self.create_query_for_sorting(q, s), sorts, query)


__all__ = [
    "SortFilterRunner",
    "FilterModel",
    "SortModel",
    "FILTER_SORT_ENTITY_ATTR_MAP",
    "INHERITS_BASE_ENTITY",
]
