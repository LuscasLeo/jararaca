# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Awaitable, Callable, Literal, Mapping, Protocol, TypeVar

from fastapi import APIRouter
from fastapi import Depends as DependsF
from fastapi.exceptions import FastAPIError
from fastapi.params import Depends

from jararaca.lifecycle import AppLifecycle
from jararaca.presentation.http_microservice import HttpMiddleware
from jararaca.presentation.websocket.decorators import WebSocketEndpoint
from jararaca.reflect.controller_inspect import (
    ControllerMemberReflect,
    inspect_controller,
)
from jararaca.reflect.decorators import DECORATED_T, StackableDecorator

DECORATED_TYPE = TypeVar("DECORATED_TYPE", bound=Any)
DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Any])


ControllerOptions = Mapping[str, Any]


class RouterFactory(Protocol):

    def __call__(self, lifecycle: AppLifecycle, instance: Any) -> APIRouter: ...


class RestController(StackableDecorator):
    REST_CONTROLLER_ATTR = "__rest_controller__"

    def __init__(
        self,
        path: str = "",
        *,
        options: ControllerOptions | None = None,
        middlewares: list[type[HttpMiddleware]] = [],
        router_factory: RouterFactory | None = None,
        class_inherits_decorators: bool = True,
        methods_inherit_decorators: bool = True,
    ) -> None:
        self.path = path
        self.options = options
        self.router_factory = router_factory
        self.middlewares = middlewares
        self.class_inherits_decorators = class_inherits_decorators
        self.methods_inherit_decorators = methods_inherit_decorators

    def get_router_factory(
        self,
    ) -> RouterFactory:
        if self.router_factory is None:
            raise Exception("Router factory is not set")
        return self.router_factory

    def post_decorated(self, subject: DECORATED_T) -> None:

        def router_factory(
            lifecycle: AppLifecycle,
            instance: DECORATED_T,
        ) -> APIRouter:
            assert inspect.isclass(
                subject
            ), "RestController can only be applied to classes"
            dependencies: list[Depends] = []

            for self_middleware_type in self.middlewares:
                middleware_instance = lifecycle.container.get_by_type(
                    self_middleware_type
                )
                dependencies.append(Depends(middleware_instance.intercept))

            class_middlewares = UseMiddleware.get_all_from_type(
                subject, self.class_inherits_decorators
            )
            for middlewares_by_hook in class_middlewares:
                middleware_instance = lifecycle.container.get_by_type(
                    middlewares_by_hook.middleware
                )
                dependencies.append(Depends(middleware_instance.intercept))

            class_dependencies = UseDependency.get_all_from_type(
                subject, self.class_inherits_decorators
            )
            for dependency in class_dependencies:
                dependencies.append(DependsF(dependency.dependency))

            router = APIRouter(
                prefix=self.path,
                dependencies=dependencies,
                **(self.options or {}),
            )

            controller, members = inspect_controller(subject)

            router_members = [
                (name, mapping, member)
                for name, member in members.items()
                if (
                    mapping := HttpMapping.get_bound_from_method(
                        subject,
                        name,
                        self.methods_inherit_decorators,
                        last=True,
                    )
                    or WebSocketEndpoint.get_bound_from_method(
                        subject,
                        name,
                        self.methods_inherit_decorators,
                        last=True,
                    )
                )
                is not None
            ]

            router_members.sort(key=lambda x: x[1].order)

            for name, mapping, member in router_members:
                route_dependencies: list[Depends] = []

                method_middlewares = UseMiddleware.get_all_from_method(
                    subject, name, self.methods_inherit_decorators
                )
                for middlewares_by_hook in method_middlewares:
                    middleware_instance = lifecycle.container.get_by_type(
                        middlewares_by_hook.middleware
                    )
                    route_dependencies.append(Depends(middleware_instance.intercept))

                method_dependencies = UseDependency.get_all_from_method(
                    subject,
                    name,
                    self.methods_inherit_decorators,
                )
                for dependency in method_dependencies:
                    route_dependencies.append(DependsF(dependency.dependency))

                instance_method = getattr(instance, name)
                instance_method = wraps_with_attributes(
                    instance_method,
                    controller_member_reflect=member,
                )

                if isinstance(mapping, HttpMapping):
                    try:
                        router.add_api_route(
                            methods=[mapping.method],
                            path=mapping.path,
                            endpoint=instance_method,
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
                        endpoint=instance_method,
                        dependencies=route_dependencies,
                        **(mapping.options or {}),
                    )

            return router

        self.router_factory = router_factory


Options = dict[str, Any]

ResponseType = Literal[
    "arraybuffer",
    "blob",
    "document",
    "json",
    "text",
    "stream",
    "formdata",
]


class HttpMapping(StackableDecorator):

    ORDER_COUNTER = 0

    def __init__(
        self,
        method: str,
        path: str = "/",
        adapter_options: Options | None = None,
        response_type: ResponseType | None = None,
    ) -> None:
        self.method = method
        self.path = path
        self.options = adapter_options
        self.response_type = response_type

        HttpMapping.ORDER_COUNTER += 1
        self.order = HttpMapping.ORDER_COUNTER

    @classmethod
    def decorator_key(cls) -> "type[HttpMapping]":
        return HttpMapping

    # def __call__(self, func: DECORATED_FUNC) -> DECORATED_FUNC:

    #     HttpMapping.register(func, self)

    #     return func

    # @staticmethod
    # def register(func: DECORATED_FUNC, mapping: "HttpMapping") -> None:

    #     setattr(func, HttpMapping.HTTP_MAPPING_ATTR, mapping)


class Post(HttpMapping):

    def __init__(
        self,
        path: str = "/",
        options: Options | None = None,
        response_type: ResponseType | None = None,
    ) -> None:
        super().__init__("POST", path, options, response_type)


class Get(HttpMapping):

    def __init__(
        self,
        path: str = "/",
        options: Options | None = None,
        response_type: ResponseType | None = None,
    ) -> None:
        super().__init__("GET", path, options, response_type)


class Put(HttpMapping):

    def __init__(
        self,
        path: str = "/",
        options: Options | None = None,
        response_type: ResponseType | None = None,
    ) -> None:
        super().__init__("PUT", path, options, response_type)


class Delete(HttpMapping):

    def __init__(
        self,
        path: str = "/",
        options: Options | None = None,
        response_type: ResponseType | None = None,
    ) -> None:
        super().__init__("DELETE", path, options, response_type)


class Patch(HttpMapping):

    def __init__(
        self,
        path: str = "/",
        options: Options | None = None,
        response_type: ResponseType | None = None,
    ) -> None:
        super().__init__("PATCH", path, options, response_type)


class UseMiddleware(StackableDecorator):

    def __init__(self, middleware: type[HttpMiddleware]) -> None:
        self.middleware = middleware


class UseDependency(StackableDecorator):

    def __init__(self, dependency: Any) -> None:
        self.dependency = dependency


def compose_route_decorators(
    *decorators: UseMiddleware | UseDependency,
    reverse: bool = False,
) -> Callable[[DECORATED_TYPE], DECORATED_TYPE]:
    """
    Compose multiple route decorators (middlewares/dependencies) into a single decorator.

    Args:
        *decorators (UseMiddleware | UseDependency): The decorators to compose.
        reverse (bool): Whether to apply the decorators in reverse order. Warning: This may affect the order of middleware execution.

    Returns:
        Callable[[DECORATED_TYPE], DECORATED_TYPE]: A single decorator that applies all the given decorators.

    Example:
        IsWorkspaceAdmin = compose_route_decorators(
            UseMiddleware(AuthMiddleware),
            UseMiddleware(IsWorkspaceScoped),
            UseMiddleware(RequiresAdminRole),
        )
    """

    def composed_decorator(func: DECORATED_TYPE) -> DECORATED_TYPE:
        for decorator in reversed(decorators) if reverse else decorators:
            func = decorator(func)
        return func

    return composed_decorator


def wraps_with_member_data(
    controller_member: ControllerMemberReflect, func: Callable[..., Awaitable[Any]]
) -> Callable[..., Any]:
    """
    A decorator that wraps a function and preserves its metadata.
    This is useful for preserving metadata when using decorators.
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:

        with providing_controller_member(
            controller_member=controller_member,
        ):

            return await func(*args, **kwargs)

    return wrapper


controller_member_ctxvar = ContextVar[ControllerMemberReflect](
    "controller_member_ctxvar"
)


@contextmanager
def providing_controller_member(
    controller_member: ControllerMemberReflect,
) -> Any:
    """
    Context manager to provide the controller member metadata.
    This is used to preserve the metadata of the controller member
    when using decorators.
    """
    token = controller_member_ctxvar.set(controller_member)
    try:
        yield
    finally:
        controller_member_ctxvar.reset(token)


def use_controller_member() -> ControllerMemberReflect:
    """
    Get the current controller member metadata.
    This is used to access the metadata of the controller member
    when using decorators.
    """
    return controller_member_ctxvar.get()


def wraps_with_attributes(
    func: Callable[..., Awaitable[Any]], **attributes: Any
) -> Callable[..., Awaitable[Any]]:
    """
    A decorator that wraps a function and preserves its attributes.
    This is useful for preserving attributes when using decorators.
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return await func(*args, **kwargs)

    # Copy attributes from the original function to the wrapper
    for key, value in attributes.items():
        setattr(wrapper, key, value)

    return wrapper
