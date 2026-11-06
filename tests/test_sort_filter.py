# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""FilterRuleApplier / SortRuleApplier behaviour, executed against in-memory SQLite."""

from typing import Generator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Uuid, create_engine, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from jararaca import BaseEntity
from jararaca.persistence.sort_filter import (
    FilterModel,
    FilterRuleApplier,
    InvalidFilterError,
    InvalidSortError,
    SortModel,
    SortRuleApplier,
)


class Ticket(BaseEntity):
    __tablename__ = "sort_filter_tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()
    count: Mapped[int | None] = mapped_column(nullable=True)
    step_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


STEP_A = uuid4()
STEP_B = uuid4()
STEP_C = uuid4()

ALLOWED = {
    "name": Ticket.name,
    "count": Ticket.count,
    "step_id": Ticket.step_id,
}


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://")
    BaseEntity.metadata.create_all(engine, tables=[Ticket.__table__])  # type: ignore[list-item]
    with Session(engine) as s:
        s.add_all(
            [
                Ticket(id=1, name="a", count=1, step_id=STEP_A),
                Ticket(id=2, name="b", count=2, step_id=STEP_B),
                Ticket(id=3, name="c", count=3, step_id=STEP_C),
                Ticket(id=4, name="d", count=None, step_id=None),
            ]
        )
        s.commit()
        yield s
    engine.dispose()


def run(session: Session, *filters: FilterModel) -> list[int]:
    query = FilterRuleApplier(ALLOWED).create_query_for_filter_list(  # type: ignore[arg-type]
        select(Ticket), list(filters)
    )
    return sorted(t.id for t in session.scalars(query))


class TestUuidFilter:
    def test_equals(self, session: Session) -> None:
        f = FilterModel(field="step_id", operator="equals", value=str(STEP_A))
        assert run(session, f) == [1]

    def test_is(self, session: Session) -> None:
        f = FilterModel(field="step_id", operator="is", value=str(STEP_B))
        assert run(session, f) == [2]

    def test_equals_accepts_uppercase_and_unhyphenated(self, session: Session) -> None:
        f = FilterModel(field="step_id", operator="equals", value=STEP_A.hex.upper())
        assert run(session, f) == [1]

    def test_does_not_equal(self, session: Session) -> None:
        f = FilterModel(field="step_id", operator="doesNotEqual", value=str(STEP_A))
        # SQL semantics: NULL rows are not "different from" a value.
        assert run(session, f) == [2, 3]

    def test_not(self, session: Session) -> None:
        f = FilterModel(field="step_id", operator="not", value=str(STEP_B))
        assert run(session, f) == [1, 3]

    def test_is_any_of(self, session: Session) -> None:
        f = FilterModel(
            field="step_id", operator="isAnyOf", value=[str(STEP_A), str(STEP_C)]
        )
        assert run(session, f) == [1, 3]

    def test_is_any_of_empty_list_is_a_no_op(self, session: Session) -> None:
        f = FilterModel(field="step_id", operator="isAnyOf", value=[])
        assert run(session, f) == [1, 2, 3, 4]

    def test_equals_empty_value_is_a_no_op(self, session: Session) -> None:
        f = FilterModel(field="step_id", operator="equals", value="")
        assert run(session, f) == [1, 2, 3, 4]

    def test_is_empty_without_value(self, session: Session) -> None:
        f = FilterModel(field="step_id", operator="isEmpty")
        assert run(session, f) == [4]

    def test_is_not_empty_without_value(self, session: Session) -> None:
        f = FilterModel(field="step_id", operator="isNotEmpty")
        assert run(session, f) == [1, 2, 3]

    def test_is_empty_ignores_value(self, session: Session) -> None:
        f = FilterModel(field="step_id", operator="isEmpty", value="whatever")
        assert run(session, f) == [4]

    @pytest.mark.parametrize("operator", ["equals", "is", "doesNotEqual", "not"])
    def test_invalid_uuid(self, session: Session, operator: str) -> None:
        f = FilterModel(field="step_id", operator=operator, value="not-a-uuid")  # type: ignore[arg-type]
        with pytest.raises(InvalidFilterError) as exc_info:
            run(session, f)
        assert exc_info.value.status_code == 422
        assert "step_id" in exc_info.value.detail

    def test_invalid_uuid_in_list(self, session: Session) -> None:
        f = FilterModel(
            field="step_id", operator="isAnyOf", value=[str(STEP_A), "nope"]
        )
        with pytest.raises(InvalidFilterError):
            run(session, f)

    def test_unsupported_operator(self, session: Session) -> None:
        f = FilterModel(field="step_id", operator="contains", value=str(STEP_A))
        with pytest.raises(InvalidFilterError):
            run(session, f)


class TestClientErrorsAreHttp4xx:
    def test_unknown_field(self, session: Session) -> None:
        f = FilterModel(field="nope", operator="equals", value="x")
        with pytest.raises(InvalidFilterError):
            run(session, f)

    def test_unsupported_string_operator(self, session: Session) -> None:
        f = FilterModel(field="name", operator=">", value="x")
        with pytest.raises(InvalidFilterError):
            run(session, f)

    def test_invalid_integer(self, session: Session) -> None:
        f = FilterModel(field="count", operator="=", value="abc")
        with pytest.raises(InvalidFilterError):
            run(session, f)

    def test_unknown_sort_field(self) -> None:
        with pytest.raises(InvalidSortError):
            SortRuleApplier(ALLOWED).create_query_for_sorting(  # type: ignore[arg-type]
                select(Ticket), SortModel(field="nope", direction="asc")
            )

    def test_still_a_value_error_for_existing_callers(self) -> None:
        assert issubclass(InvalidFilterError, ValueError)
        assert issubclass(InvalidSortError, ValueError)

    def test_fastapi_maps_invalid_filter_to_422(self) -> None:
        app = FastAPI()

        @app.post("/query")
        def query(f: FilterModel) -> list[int]:
            FilterRuleApplier(ALLOWED).create_query_for_filter(select(Ticket), f)  # type: ignore[arg-type]
            return []

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/query",
                json={"field": "step_id", "operator": "equals", "value": "bad"},
            )
        assert response.status_code == 422
        assert "step_id" in response.json()["detail"]


class TestExistingTypesUnchanged:
    def test_string_equals(self, session: Session) -> None:
        f = FilterModel(field="name", operator="equals", value="b")
        assert run(session, f) == [2]

    def test_int_comparison(self, session: Session) -> None:
        f = FilterModel(field="count", operator=">=", value="2")
        assert run(session, f) == [2, 3]
