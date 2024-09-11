from dataclasses import dataclass, field
from typing import Any, AsyncContextManager, Callable, Protocol

from fastapi import FastAPI
from starlette.types import Lifespan

from jararaca.microservice import Microservice


class HttpMiddleware(Protocol):

    def intercept(self, *args: Any, **kwargs: Any) -> AsyncContextManager[None]: ...


@dataclass
class HttpMicroservice:
    app: Microservice
    factory: Callable[[Lifespan[FastAPI]], FastAPI] | None = None
    middlewares: list[type[HttpMiddleware]] = field(default_factory=list)
