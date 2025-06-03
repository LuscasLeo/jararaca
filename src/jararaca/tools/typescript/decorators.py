from typing import Any, Callable, TypeVar, cast

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
