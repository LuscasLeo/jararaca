import inspect
from typing import Any, Callable, Protocol, TypeVar, cast

from fastapi import APIRouter
from fastapi import Depends as DependsF
from fastapi.exceptions import FastAPIError
from fastapi.params import Depends

from jararaca.lifecycle import AppLifecycle
from jararaca.presentation.http_microservice import HttpMiddleware
from jararaca.presentation.websocket.decorators import WebSocketEndpoint

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Any])
DECORATED_CLASS = TypeVar("DECORATED_CLASS", bound=Any)


ControllerOptions = dict[str, Any]


class RouterFactory(Protocol):

    def __call__(self, lifecycle: AppLifecycle, instance: Any) -> APIRouter: ...


class RestController:
    REST_CONTROLLER_ATTR = "__rest_controller__"

    def __init__(
        self,
        path: str = "",
        options: ControllerOptions | None = None,
        middlewares: list[type[HttpMiddleware]] = [],
        router_factory: RouterFactory | None = None,
    ) -> None:
        self.path = path
        self.options = options
        self.router_factory = router_factory
        self.middlewares = middlewares

    def get_router_factory(
        self,
    ) -> RouterFactory:
        if self.router_factory is None:
            raise Exception("Router factory is not set")
        return self.router_factory

    def __call__(self, cls: type[DECORATED_CLASS]) -> type[DECORATED_CLASS]:

        def router_factory(
            lifecycle: AppLifecycle,
            instance: DECORATED_CLASS,
        ) -> APIRouter:
            dependencies: list[Depends] = []

            for self_middleware_type in self.middlewares:
                middleware_instance = lifecycle.container.get_by_type(
                    self_middleware_type
                )
                dependencies.append(Depends(middleware_instance.intercept))

            for middlewares_by_hook in UseMiddleware.get_middlewares(instance):
                middleware_instance = lifecycle.container.get_by_type(
                    middlewares_by_hook.middleware
                )
                dependencies.append(Depends(middleware_instance.intercept))

            for dependency in UseDependency.get_dependencies(instance):
                dependencies.append(DependsF(dependency.dependency))

            router = APIRouter(
                prefix=self.path,
                dependencies=dependencies,
                **(self.options or {}),
            )

            members = inspect.getmembers(cls, predicate=inspect.isfunction)

            router_members = [
                (name, mapping)
                for name, member in members
                if (
                    mapping := (
                        HttpMapping.get_http_mapping(member)
                        or WebSocketEndpoint.get(member)
                    )
                )
                is not None
            ]

            router_members.sort(key=lambda x: x[1].order)

            for name, mapping in router_members:
                route_dependencies: list[Depends] = []
                for middlewares_by_hook in UseMiddleware.get_middlewares(
                    getattr(instance, name)
                ):
                    middleware_instance = lifecycle.container.get_by_type(
                        middlewares_by_hook.middleware
                    )
                    route_dependencies.append(Depends(middleware_instance.intercept))

                for dependency in UseDependency.get_dependencies(
                    getattr(instance, name)
                ):
                    route_dependencies.append(DependsF(dependency.dependency))

                if isinstance(mapping, HttpMapping):
                    try:
                        router.add_api_route(
                            methods=[mapping.method],
                            path=mapping.path,
                            endpoint=getattr(instance, name),
                            dependencies=route_dependencies,
                            **(mapping.options or {}),
                        )
                    except FastAPIError as e:
                        raise Exception(
                            f"Error while adding route {mapping.path}"
                        ) from e
                else:
                    router.add_api_websocket_route(
                        path=mapping.path,
                        endpoint=getattr(instance, name),
                        dependencies=route_dependencies,
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


Options = dict[str, Any]


class HttpMapping:

    HTTP_MAPPING_ATTR = "__http_mapping__"
    ORDER_COUNTER = 0

    def __init__(
        self, method: str, path: str = "/", options: Options | None = None
    ) -> None:
        self.method = method
        self.path = path
        self.options = options

        HttpMapping.ORDER_COUNTER += 1
        self.order = HttpMapping.ORDER_COUNTER

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

    def __init__(self, path: str = "/", options: Options | None = None) -> None:
        super().__init__("POST", path, options)


class Get(HttpMapping):

    def __init__(self, path: str = "/", options: Options | None = None) -> None:
        super().__init__("GET", path, options)


class Put(HttpMapping):

    def __init__(self, path: str = "/", options: Options | None = None) -> None:
        super().__init__("PUT", path, options)


class Delete(HttpMapping):

    def __init__(self, path: str = "/", options: Options | None = None) -> None:
        super().__init__("DELETE", path, options)


class Patch(HttpMapping):

    def __init__(self, path: str = "/", options: Options | None = None) -> None:
        super().__init__("PATCH", path, options)


class UseMiddleware:

    __MIDDLEWARES_ATTR__ = "__middlewares__"

    def __init__(self, middleware: type[HttpMiddleware]) -> None:
        self.middleware = middleware

    def __call__(self, subject: DECORATED_FUNC) -> DECORATED_FUNC:

        UseMiddleware.register(subject, self)

        return subject

    @staticmethod
    def register(subject: DECORATED_FUNC, middleware: "UseMiddleware") -> None:
        middlewares = getattr(subject, UseMiddleware.__MIDDLEWARES_ATTR__, [])
        middlewares.append(middleware)
        setattr(subject, UseMiddleware.__MIDDLEWARES_ATTR__, middlewares)

    @staticmethod
    def get_middlewares(subject: DECORATED_FUNC) -> list["UseMiddleware"]:
        return getattr(subject, UseMiddleware.__MIDDLEWARES_ATTR__, [])


class UseDependency:

    __DEPENDENCY_ATTR__ = "__dependencies__"

    def __init__(self, dependency: Any) -> None:
        self.dependency = dependency

    def __call__(self, subject: DECORATED_FUNC) -> DECORATED_FUNC:

        UseDependency.register(subject, self)

        return subject

    @staticmethod
    def register(subject: DECORATED_FUNC, dependency: "UseDependency") -> None:
        dependencies = getattr(subject, UseDependency.__DEPENDENCY_ATTR__, [])
        dependencies.append(dependency)
        setattr(subject, UseDependency.__DEPENDENCY_ATTR__, dependencies)

    @staticmethod
    def get_dependencies(subject: DECORATED_FUNC) -> list["UseDependency"]:
        return getattr(subject, UseDependency.__DEPENDENCY_ATTR__, [])
