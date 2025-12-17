# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from jararaca.reflect.decorators import StackableDecorator

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Any])


class QueryEndpoint(StackableDecorator):
    """
    Decorator to mark a endpoint function as a query endpoint for Typescript generation.
    """

    def __init__(self, has_infinite_query: bool = False) -> None:
        """
        Initialize the QueryEndpoint decorator.

        Args:
            has_infinite_query: Whether the query endpoint supports infinite queries.
            Important:
            - Make sure a PaginatedQuery child instance is on the first argument
            - Make sure the endpoint is a Patch (recommended) or Put method
            - Make sure the endpoint returns a Paginated[T]
        """
        self.has_infinite_query = has_infinite_query

    @staticmethod
    def extract_query_endpoint(func: Any) -> "QueryEndpoint | None":
        """
        Check if the function is marked as a query endpoint.
        """
        return QueryEndpoint.get_last(func)


class MutationEndpoint(StackableDecorator):
    """
    Decorator to mark a endpoint function as a mutation endpoint for Typescript generation.
    """

    def __init__(self) -> None: ...

    @staticmethod
    def is_mutation(func: Any) -> bool:
        """
        Check if the function is marked as a mutation endpoint.
        """
        return MutationEndpoint.get_last(func) is not None


BASEMODEL_T = TypeVar("BASEMODEL_T", bound=BaseModel)


class SplitInputOutput(StackableDecorator):
    """
    Decorator to mark a Pydantic model to generate separate Input and Output TypeScript interfaces.

    Input interface: Used for API inputs (mutations/queries), handles optional fields with defaults
    Output interface: Used for API outputs, represents the complete object structure
    """

    def __init__(self) -> None:
        pass

    @staticmethod
    def is_split_model(cls: type) -> bool:
        """
        Check if the Pydantic model is marked for split interface generation.
        """
        return SplitInputOutput.get_last(cls) is not None


class ExposeType:
    """
    Decorator to explicitly expose types for TypeScript interface generation.

    Use this decorator to include types in the generated TypeScript output without
    needing them as request/response bodies or indirect dependencies.

    Example:
        @ExposeType()
        class UserRole(BaseModel):
            id: str
            name: str

        # This ensures UserRole interface is generated even if it's not
        # directly referenced in any REST endpoint
    """

    METADATA_KEY = "__jararaca_expose_type__"
    _exposed_types: set[type] = set()

    def __init__(self) -> None:
        pass

    def __call__(self, cls: type[BASEMODEL_T]) -> type[BASEMODEL_T]:
        """
        Decorate the type to mark it for explicit TypeScript generation.
        """
        setattr(cls, self.METADATA_KEY, True)
        ExposeType._exposed_types.add(cls)
        return cls

    @staticmethod
    def is_exposed_type(cls: type) -> bool:
        """
        Check if the type is marked for explicit exposure.
        """
        return getattr(cls, ExposeType.METADATA_KEY, False)

    @staticmethod
    def get_all_exposed_types() -> set[type]:
        """
        Get all types that have been marked for explicit exposure.
        """
        return ExposeType._exposed_types.copy()
