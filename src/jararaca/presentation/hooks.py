from contextlib import contextmanager
from typing import Any, ContextManager, Generator, Type

from fastapi import HTTPException


@contextmanager
def raises_http_exception_on(
    exc_type: Type[BaseException],
    status_code: int = 500,
    details: Any = "Internal server error",
) -> Generator[None, None, None]:
    try:
        yield
    except exc_type:
        raise HTTPException(
            status_code=status_code,
            detail=details,
        )


def raises_200_on(
    exc_type: Type[BaseException], details: Any = "OK"
) -> ContextManager[None]:
    """Raises a 200 HTTP exception when the decorated function raises the specified exception. Please note that this is not a common use case. (Don't judge me)"""
    return raises_http_exception_on(exc_type, status_code=200, details=details)


def raises_422_on(
    exc_type: Type[BaseException], details: Any = "Unprocessable entity"
) -> ContextManager[None]:
    return raises_http_exception_on(exc_type, status_code=422, details=details)


def raises_404_on(
    exc_type: Type[BaseException], details: Any = "Not found"
) -> ContextManager[None]:
    return raises_http_exception_on(exc_type, status_code=404, details=details)


def raises_400_on(
    exc_type: Type[BaseException], details: Any = "Bad request"
) -> ContextManager[None]:
    return raises_http_exception_on(exc_type, status_code=400, details=details)


def raises_401_on(
    exc_type: Type[BaseException], details: Any = "Unauthorized"
) -> ContextManager[None]:
    return raises_http_exception_on(exc_type, status_code=401, details=details)


def raises_403_on(
    exc_type: Type[BaseException], details: Any = "Forbidden"
) -> ContextManager[None]:
    return raises_http_exception_on(exc_type, status_code=403, details=details)


def raises_500_on(
    exc_type: Type[BaseException], details: Any = "Internal server error"
) -> ContextManager[None]:
    return raises_http_exception_on(exc_type, status_code=500, details=details)
