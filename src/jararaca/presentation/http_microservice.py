from dataclasses import dataclass
from typing import Callable

from fastapi import FastAPI
from starlette.types import Lifespan

from jararaca.microservice import Microservice


@dataclass
class HttpMicroservice:
    app: Microservice
    factory: Callable[[Lifespan[FastAPI]], FastAPI] | None = None
