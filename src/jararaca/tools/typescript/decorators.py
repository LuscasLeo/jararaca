from typing import Any, Callable, TypeVar

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Any])


class QueryEndpoint:
    """
    Decorator to mark a endpoint function as a query endpoint for Typescript generation.
    """

    METADATA_KEY = "__jararaca_query_endpoint__"

    def __init__(self) -> None: ...

    def __call__(self, func: DECORATED_FUNC) -> DECORATED_FUNC:
        """
        Decorate the function to mark it as a query endpoint.
        """
        setattr(func, self.METADATA_KEY, True)
        return func

    @staticmethod
    def is_query(func: Any) -> bool:
        """
        Check if the function is marked as a query endpoint.
        """
        return getattr(func, QueryEndpoint.METADATA_KEY, False)


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
