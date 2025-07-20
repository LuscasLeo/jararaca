from typing import Any, Callable, TypeVar, cast

from pydantic import BaseModel

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Any])


class QueryEndpoint:
    """
    Decorator to mark a endpoint function as a query endpoint for Typescript generation.
    """

    METADATA_KEY = "__jararaca_query_endpoint__"

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

    def __call__(self, func: DECORATED_FUNC) -> DECORATED_FUNC:
        """
        Decorate the function to mark it as a query endpoint.
        """
        setattr(func, self.METADATA_KEY, self)
        return func

    @staticmethod
    def extract_query_endpoint(func: Any) -> "QueryEndpoint":
        """
        Check if the function is marked as a query endpoint.
        """
        return cast(QueryEndpoint, getattr(func, QueryEndpoint.METADATA_KEY, None))


class MutationEndpoint:
    """
    Decorator to mark a endpoint function as a mutation endpoint for Typescript generation.
    """

    METADATA_KEY = "__jararaca_mutation_endpoint__"

    def __init__(self) -> None: ...

    def __call__(self, func: DECORATED_FUNC) -> DECORATED_FUNC:
        """
        Decorate the function to mark it as a mutation endpoint.
        """
        setattr(func, self.METADATA_KEY, True)
        return func

    @staticmethod
    def is_mutation(func: Any) -> bool:
        """
        Check if the function is marked as a mutation endpoint.
        """
        return getattr(func, MutationEndpoint.METADATA_KEY, False)


BASEMODEL_T = TypeVar("BASEMODEL_T", bound=BaseModel)


class SplitInputOutput:
    """
    Decorator to mark a Pydantic model to generate separate Input and Output TypeScript interfaces.

    Input interface: Used for API inputs (mutations/queries), handles optional fields with defaults
    Output interface: Used for API outputs, represents the complete object structure
    """

    METADATA_KEY = "__jararaca_split_input_output__"

    def __init__(self) -> None:
        pass

    def __call__(self, cls: type[BASEMODEL_T]) -> type[BASEMODEL_T]:
        """
        Decorate the Pydantic model class to mark it for split interface generation.
        """
        setattr(cls, self.METADATA_KEY, True)
        return cls

    @staticmethod
    def is_split_model(cls: type) -> bool:
        """
        Check if the Pydantic model is marked for split interface generation.
        """
        return getattr(cls, SplitInputOutput.METADATA_KEY, False)
