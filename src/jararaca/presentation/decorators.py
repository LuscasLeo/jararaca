import inspect
from typing import Any, Callable, ParamSpec, TypedDict, TypeVar, cast

from fastapi import APIRouter

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Any])
DECORATED_CLASS = TypeVar("DECORATED_CLASS", bound=type)


class ControllerOptions(TypedDict): ...


class RestController:
    REST_CONTROLLER_ATTR = "__rest_controller__"

    def __init__(
        self, path: str = "", options: ControllerOptions | None = None
    ) -> None:
        self.path = path
        self.options = options
        self.router_factory: Callable[[Any], APIRouter] | None = None

    def get_router_factory(self) -> Callable[[DECORATED_CLASS], APIRouter]:
        if self.router_factory is None:
            raise Exception("Router factory is not set")
        return self.router_factory

    def __call__(self, cls: type[DECORATED_CLASS]) -> type[DECORATED_CLASS]:
        # Do something with the function

        def router_factory(instance: DECORATED_CLASS) -> APIRouter:
            router = APIRouter(
                prefix=self.path,
                **(self.options or {}),
            )

            signature = inspect.signature(cls)

            members = inspect.getmembers(cls, predicate=inspect.isfunction)

            for name, member in members:
                mapping = HttpMapping.get_http_mapping(member)

                if mapping is None:
                    continue

                router.add_api_route(
                    methods=[mapping.method],
                    path=mapping.path,
                    endpoint=getattr(instance, name),
                    **(mapping.options or {}),
                )

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
