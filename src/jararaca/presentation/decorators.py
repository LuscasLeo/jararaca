import inspect
from typing import Any, Callable, TypedDict, TypeVar, cast

from fastapi import APIRouter
from fastapi.exceptions import FastAPIError
from fastapi.params import Depends

from jararaca.presentation.http_microservice import HttpMiddleware

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Any])
DECORATED_CLASS = TypeVar("DECORATED_CLASS", bound=Any)


class ControllerOptions(TypedDict): ...


class RestController:
    REST_CONTROLLER_ATTR = "__rest_controller__"

    def __init__(
        self,
        path: str = "",
        options: ControllerOptions | None = None,
        middlewares: list[type[HttpMiddleware]] = [],
        router_factory: Callable[[Any, list[Depends]], APIRouter] | None = None,
    ) -> None:
        self.path = path
        self.options = options
        self.router_factory = router_factory
        self.middlewares = middlewares

    def get_router_factory(
        self,
    ) -> Callable[[DECORATED_CLASS, list[Depends]], APIRouter]:
        if self.router_factory is None:
            raise Exception("Router factory is not set")
        return self.router_factory

    def __call__(self, cls: type[DECORATED_CLASS]) -> type[DECORATED_CLASS]:

        def router_factory(
            instance: DECORATED_CLASS, dependencies: list[Depends]
        ) -> APIRouter:
            router = APIRouter(
                prefix=self.path,
                dependencies=dependencies,
                **(self.options or {}),
            )

            members = inspect.getmembers(cls, predicate=inspect.isfunction)

            for name, member in members:

                if (mapping := HttpMapping.get_http_mapping(member)) is not None:

                    try:
                        router.add_api_route(
                            methods=[mapping.method],
                            path=mapping.path,
                            endpoint=getattr(instance, name),
                            **(mapping.options or {}),
                        )
                    except FastAPIError as e:
                        raise Exception(
                            f"Error while adding route {mapping.path}"
                        ) from e

            return router

        self.router_factory = router_factory

        RestController.register(cls, self)

        return cls

    @staticmethod
    def register(cls: type[DECORATED_CLASS], controller: "RestController") -> None:
        setattr(cls, RestController.REST_CONTROLLER_ATTR, controller)

    @staticmethod
    def get_controller(cls: type[DECORATED_CLASS]) -> "RestController | None":
        if not hasattr(cls, RestController.REST_CONTROLLER_ATTR):
            return None

        return cast(RestController, getattr(cls, RestController.REST_CONTROLLER_ATTR))


class Options(TypedDict): ...


class HttpMapping:

    HTTP_MAPPING_ATTR = "__http_mapping__"

    def __init__(self, method: str, path: str, options: Options | None = None) -> None:
        self.method = method
        self.path = path
        self.options = options

    def __call__(self, func: DECORATED_FUNC) -> DECORATED_FUNC:

        HttpMapping.register(func, self)

        return func

    @staticmethod
    def register(func: DECORATED_FUNC, mapping: "HttpMapping") -> None:

        setattr(func, HttpMapping.HTTP_MAPPING_ATTR, mapping)

    @staticmethod
    def get_http_mapping(func: DECORATED_FUNC) -> "HttpMapping | None":

        if not hasattr(func, HttpMapping.HTTP_MAPPING_ATTR):
            return None

        return cast(HttpMapping, getattr(func, HttpMapping.HTTP_MAPPING_ATTR))


class Post(HttpMapping):

    def __init__(self, path: str, options: Options | None = None) -> None:
        super().__init__("POST", path, options)


class Get(HttpMapping):

    def __init__(self, path: str, options: Options | None = None) -> None:
        super().__init__("GET", path, options)


class Put(HttpMapping):

    def __init__(self, path: str, options: Options | None = None) -> None:
        super().__init__("PUT", path, options)


class Delete(HttpMapping):

    def __init__(self, path: str, options: Options | None = None) -> None:
        super().__init__("DELETE", path, options)


class Patch(HttpMapping):

    def __init__(self, path: str, options: Options | None = None) -> None:
        super().__init__("PATCH", path, options)
