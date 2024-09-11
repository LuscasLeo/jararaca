import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol, Type, TypeVar, cast

from pydantic import BaseModel

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Awaitable[Any]])


class HttpMapping:

    HTTP_MAPPING_ATTR = "__rest_http_client_mapping__"

    def __init__(self, method: str, path: str, success_status: int = 200):
        self.method = method
        self.path = path
        self.success_status = success_status

    def __call__(self, func: DECORATED_FUNC) -> DECORATED_FUNC:
        HttpMapping.register(func, self)
        return func

    @staticmethod
    def register(funf: DECORATED_FUNC, instance: "HttpMapping") -> None:
        setattr(funf, HttpMapping.HTTP_MAPPING_ATTR, instance)

    @staticmethod
    def get(funf: DECORATED_FUNC) -> "HttpMapping | None":
        if hasattr(funf, HttpMapping.HTTP_MAPPING_ATTR):
            return cast(HttpMapping, getattr(funf, HttpMapping.HTTP_MAPPING_ATTR))

        return None


class Post(HttpMapping):

    def __init__(self, path: str):
        super().__init__("POST", path)


class Get(HttpMapping):

    def __init__(self, path: str):
        super().__init__("GET", path)


class Patch(HttpMapping):

    def __init__(self, path: str):
        super().__init__("PATCH", path)


class Put(HttpMapping):

    def __init__(self, path: str):
        super().__init__("PUT", path)


class Delete(HttpMapping):

    def __init__(self, path: str):
        super().__init__("DELETE", path)


class RequestAttribute:

    REQUEST_ATTRIBUTE_ATTRS = "__request_attributes__"

    @staticmethod
    def register(cls: DECORATED_FUNC, instance: "RequestAttribute") -> None:

        if not hasattr(cls, RequestAttribute.REQUEST_ATTRIBUTE_ATTRS):
            setattr(cls, RequestAttribute.REQUEST_ATTRIBUTE_ATTRS, [])

        getattr(cls, RequestAttribute.REQUEST_ATTRIBUTE_ATTRS).append(instance)

    @staticmethod
    def get(cls: DECORATED_FUNC) -> "list[RequestAttribute]":
        if hasattr(cls, RequestAttribute.REQUEST_ATTRIBUTE_ATTRS):
            return cast(
                list[RequestAttribute],
                getattr(cls, RequestAttribute.REQUEST_ATTRIBUTE_ATTRS),
            )

        return []

    def __init__(
        self, attribute_type: Literal["query", "header", "body", "param"], name: str
    ):
        self.attribute_type = attribute_type
        self.name = name

    def __call__(self, cls: DECORATED_FUNC) -> DECORATED_FUNC:
        RequestAttribute.register(cls, self)
        return cls


class Query(RequestAttribute):

    def __init__(self, name: str):
        super().__init__("query", name)


class Header(RequestAttribute):

    def __init__(self, name: str):
        super().__init__("header", name)


class Body(RequestAttribute):

    def __init__(self, name: str):
        super().__init__("body", name)


class PathParam(RequestAttribute):

    def __init__(self, name: str):
        super().__init__("param", name)


DECORATED_CLASS = TypeVar("DECORATED_CLASS", bound=Any)


class RestClient:

    REST_CLIENT_ATTR = "__rest_client__"

    def __init__(self, base_path: str) -> None:
        self.base_path = base_path

    @staticmethod
    def register(cls: type, instance: "RestClient") -> None:
        setattr(cls, RestClient.REST_CLIENT_ATTR, instance)

    @staticmethod
    def get(cls: type) -> "RestClient | None":
        if hasattr(cls, RestClient.REST_CLIENT_ATTR):
            return cast(RestClient, getattr(cls, RestClient.REST_CLIENT_ATTR))

        return None

    def __call__(self, cls: Type[DECORATED_CLASS]) -> Type[DECORATED_CLASS]:

        RestClient.register(cls, self)

        return cls


class RPCRequestNetworkError(Exception):
    pass


@dataclass
class HttpRPCResponse:

    status_code: int
    data: bytes


@dataclass
class HttpRPCRequest:
    url: str
    method: str
    headers: list[tuple[str, str]]
    query_params: dict[str, str]
    body: bytes | None


class HttpRPCAsyncBackend(Protocol):

    async def request(
        self,
        request: HttpRPCRequest,
    ) -> HttpRPCResponse: ...


T = TypeVar("T")


class RequestMiddleware(Protocol):

    def on_request(self, request: HttpRPCRequest) -> HttpRPCRequest: ...


from opentelemetry import baggage, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


class TracedRequestMiddleware(RequestMiddleware):

    def on_request(self, request: HttpRPCRequest) -> HttpRPCRequest:

        span = trace.get_current_span()

        if span is not None:
            ctx = baggage.set_baggage("hello", "world")
            headers: dict[str, str] = {}
            W3CBaggagePropagator().inject(headers, ctx)
            TraceContextTextMapPropagator().inject(headers, ctx)

            for key, value in headers.items():
                request.headers.append((key, value))

        return request


class HttpRpcClientBuilder:

    def __init__(
        self,
        backend: HttpRPCAsyncBackend,
        middlewares: list[RequestMiddleware] = [],
    ):
        self._backend = backend
        self._middlewares = middlewares

    def build(self, cls: type[T]) -> T:
        rest_client = RestClient.get(cls)

        if rest_client is None:
            raise ValueError("Class is not a rest client")

        def create_method(
            mapping: HttpMapping,
            method_call: Callable[..., Any],
            http_method: str,
            path: str,
        ) -> Callable[..., Awaitable[Any]]:

            call_signature = inspect.signature(method_call)
            call_parameters = [*call_signature.parameters.keys()][1:]

            async def rpc_method(*args: Any, **kwargs: Any) -> Any:

                args_as_kwargs = dict(zip(call_parameters, args))

                request_attributes = RequestAttribute.get(method_call)

                compiled_kwargs = {**args_as_kwargs, **kwargs}

                headers: list[tuple[str, str]] = []
                query_params = {}
                body: Any = None
                compiled_path = (
                    rest_client.base_path.rstrip("/") + "/" + mapping.path.lstrip("/")
                )

                for attr in request_attributes:
                    if attr.attribute_type == "header":
                        headers.append((attr.name, compiled_kwargs[attr.name]))
                    elif attr.attribute_type == "query":
                        query_params[attr.name] = compiled_kwargs[attr.name]
                    elif attr.attribute_type == "body":
                        body = compiled_kwargs[attr.name]
                    elif attr.attribute_type == "param":
                        compiled_path = path.replace(
                            f":{attr.name}", str(compiled_kwargs[attr.name])
                        )

                body_content: bytes | None = None

                if body is not None:
                    if isinstance(body, BaseModel):
                        body_content = body.model_dump_json().encode()
                        headers.append(("Content-Type", "application/json"))
                    elif isinstance(body, bytes):
                        body_content = body
                    else:
                        raise ValueError("Invalid body type")

                request = HttpRPCRequest(
                    url=compiled_path,
                    method=http_method,
                    headers=headers,
                    query_params=query_params,
                    body=body_content,
                )

                for middleware in self._middlewares:
                    request = middleware.on_request(request)

                response = await self._backend.request(request)

                return_type = inspect.signature(method_call).return_annotation

                if response.status_code != mapping.success_status:
                    raise ValueError(
                        "Invalid status code: {}".format(response.status_code)
                    )

                if return_type is not inspect.Signature.empty:
                    if issubclass(return_type, BaseModel):
                        return return_type.model_validate_json(response.data)
                    if return_type is bytes:
                        return response.data
                    if return_type is None:
                        return None

                return response

            return rpc_method

        class Dummy: ...

        dummy = Dummy()

        for attr_name in dir(cls):
            method_call = getattr(cls, attr_name)
            if (mapping := HttpMapping.get(method_call)) is not None:
                setattr(
                    dummy,
                    attr_name,
                    create_method(mapping, method_call, mapping.method, mapping.path),
                )

        return cast(T, dummy)
