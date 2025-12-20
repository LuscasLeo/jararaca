# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import inspect
import json
import logging
import time
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    Literal,
    Optional,
    Protocol,
    TypeVar,
    cast,
)

from pydantic import BaseModel

from jararaca.reflect.decorators import StackableDecorator

logger = logging.getLogger(__name__)

DECORATED_FUNC = TypeVar("DECORATED_FUNC", bound=Callable[..., Awaitable[Any]])
DECORATED_CLASS = TypeVar("DECORATED_CLASS", bound=Any)


class TimeoutException(Exception):
    """Exception raised when a request times out"""


class HttpMapping(StackableDecorator):

    def __init__(self, method: str, path: str, success_statuses: Iterable[int] = [200]):
        self.method = method
        self.path = path
        self.success_statuses = success_statuses

    @classmethod
    def decorator_key(cls) -> Any:
        return HttpMapping


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


class RequestAttribute(StackableDecorator):

    def __init__(
        self,
        attribute_type: Literal["query", "header", "body", "param", "form", "file"],
        name: str,
    ):
        self.attribute_type = attribute_type
        self.name = name

    @classmethod
    def decorator_key(cls) -> Any:
        return RequestAttribute


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


class FormData(RequestAttribute):
    """Decorator for form data parameters"""

    def __init__(self, name: str):
        super().__init__("form", name)


class File(RequestAttribute):
    """Decorator for file upload parameters"""

    def __init__(self, name: str):
        super().__init__("file", name)


class Timeout:
    """Decorator for setting request timeout"""

    TIMEOUT_ATTR = "__request_timeout__"

    def __init__(self, seconds: float):
        self.seconds = seconds

    def __call__(self, func: DECORATED_FUNC) -> DECORATED_FUNC:
        setattr(func, self.TIMEOUT_ATTR, self)
        return func

    @staticmethod
    def get(func: DECORATED_FUNC) -> Optional["Timeout"]:
        return getattr(func, Timeout.TIMEOUT_ATTR, None)


class RPCRetryPolicy:
    """Configuration for retry behavior"""

    def __init__(
        self,
        max_attempts: int = 3,
        backoff_factor: float = 1.0,
        retry_on_status_codes: Optional[list[int]] = None,
    ):
        self.max_attempts = max_attempts
        self.backoff_factor = backoff_factor
        self.retry_on_status_codes = retry_on_status_codes or [500, 502, 503, 504]


class Retry:
    """Decorator for retry configuration"""

    RETRY_ATTR = "__request_retry__"

    def __init__(self, config: RPCRetryPolicy):
        self.config = config

    def __call__(self, func: DECORATED_FUNC) -> DECORATED_FUNC:
        setattr(func, self.RETRY_ATTR, self)
        return func

    @staticmethod
    def get(func: DECORATED_FUNC) -> Optional["Retry"]:
        return getattr(func, Retry.RETRY_ATTR, None)


class ContentType:
    """Decorator for specifying content type"""

    CONTENT_TYPE_ATTR = "__content_type__"

    def __init__(self, content_type: str):
        self.content_type = content_type

    def __call__(self, func: DECORATED_FUNC) -> DECORATED_FUNC:
        setattr(func, self.CONTENT_TYPE_ATTR, self)
        return func

    @staticmethod
    def get(func: DECORATED_FUNC) -> Optional["ContentType"]:
        return getattr(func, ContentType.CONTENT_TYPE_ATTR, None)


class ResponseMiddleware(Protocol):
    """Protocol for response middleware"""

    def on_response(
        self, request: "HttpRPCRequest", response: "HttpRPCResponse"
    ) -> "HttpRPCResponse": ...


class RequestHook(Protocol):
    """Protocol for request hooks"""

    def before_request(self, request: "HttpRPCRequest") -> "HttpRPCRequest": ...


class ResponseHook(Protocol):
    """Protocol for response hooks"""

    def after_response(
        self, request: "HttpRPCRequest", response: "HttpRPCResponse"
    ) -> "HttpRPCResponse": ...


class RestClient(StackableDecorator):

    def __init__(self, base_path: str) -> None:
        self.base_path = base_path


@dataclass
class HttpRPCResponse:
    status_code: int
    data: bytes
    headers: Optional[Dict[str, str]] = None
    elapsed_time: Optional[float] = None


@dataclass
class HttpRPCRequest:
    url: str
    method: str
    headers: list[tuple[str, str]]
    query_params: dict[str, str]
    body: bytes | None
    timeout: Optional[float] = None
    form_data: Optional[Dict[str, Any]] = None
    files: Optional[Dict[str, Any]] = None


class RPCRequestNetworkError(Exception):

    def __init__(self, request: HttpRPCRequest, backend_request: Any):
        self.request = request
        self.backend_request = backend_request
        super().__init__("Network error")


class RPCUnhandleError(Exception):

    def __init__(
        self, request: HttpRPCRequest, response: HttpRPCResponse, backend_response: Any
    ):
        self.request = request
        self.response = response
        self.backend = backend_response
        super().__init__(f"Unhandle error {response.status_code}")


class HandleHttpErrorCallback(Protocol):

    def __call__(self, request: HttpRPCRequest, response: HttpRPCResponse) -> Any: ...


class GlobalHttpErrorHandler(StackableDecorator):

    def __init__(self, status_code: int, callback: HandleHttpErrorCallback):
        self.status_code = status_code
        self.callback = callback


class RouteHttpErrorHandler(StackableDecorator):

    def __init__(self, status_code: int, callback: HandleHttpErrorCallback):
        self.status_code = status_code
        self.callback = callback


class HttpRPCAsyncBackend(Protocol):

    async def request(
        self,
        request: HttpRPCRequest,
    ) -> HttpRPCResponse: ...


T = TypeVar("T")


class RequestMiddleware(Protocol):

    def on_request(self, request: HttpRPCRequest) -> HttpRPCRequest: ...


class AuthenticationMiddleware(RequestMiddleware):
    """Base class for authentication middleware"""

    def on_request(self, request: HttpRPCRequest) -> HttpRPCRequest:
        return self.add_auth(request)

    def add_auth(self, request: HttpRPCRequest) -> HttpRPCRequest:
        raise NotImplementedError


class BearerTokenAuth(AuthenticationMiddleware):
    """Bearer token authentication middleware"""

    def __init__(self, token: str):
        self.token = token

    def add_auth(self, request: HttpRPCRequest) -> HttpRPCRequest:
        request.headers.append(("Authorization", f"Bearer {self.token}"))
        return request


class BasicAuth(AuthenticationMiddleware):
    """Basic authentication middleware"""

    def __init__(self, username: str, password: str):
        import base64

        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.credentials = credentials

    def add_auth(self, request: HttpRPCRequest) -> HttpRPCRequest:
        request.headers.append(("Authorization", f"Basic {self.credentials}"))
        return request


class ApiKeyAuth(AuthenticationMiddleware):
    """API key authentication middleware"""

    def __init__(self, api_key: str, header_name: str = "X-API-Key"):
        self.api_key = api_key
        self.header_name = header_name

    def add_auth(self, request: HttpRPCRequest) -> HttpRPCRequest:
        request.headers.append((self.header_name, self.api_key))
        return request


class CacheMiddleware(RequestMiddleware):
    """Simple in-memory cache middleware"""

    def __init__(self, ttl_seconds: int = 300):
        self.cache: Dict[str, tuple[Any, float]] = {}
        self.ttl_seconds = ttl_seconds

    def _cache_key(self, request: HttpRPCRequest) -> str:
        """Generate cache key from request"""
        key_data = {
            "method": request.method,
            "url": request.url,
            "query_params": request.query_params,
            "headers": sorted(request.headers),
        }
        return str(hash(json.dumps(key_data, sort_keys=True)))

    def on_request(self, request: HttpRPCRequest) -> HttpRPCRequest:
        # Only cache GET requests
        if request.method == "GET":
            cache_key = self._cache_key(request)
            if cache_key in self.cache:
                cached_response, timestamp = self.cache[cache_key]
                if time.time() - timestamp < self.ttl_seconds:
                    # Return cached response (this needs to be handled in the client builder)
                    setattr(request, "_cached_response", cached_response)
        return request


class HttpRpcClientBuilder:

    def __init__(
        self,
        backend: HttpRPCAsyncBackend,
        middlewares: list[RequestMiddleware] = [],
        response_middlewares: list[ResponseMiddleware] = [],
        request_hooks: list[RequestHook] = [],
        response_hooks: list[ResponseHook] = [],
    ):
        self._backend = backend
        self._middlewares = middlewares
        self._response_middlewares = response_middlewares
        self._request_hooks = request_hooks
        self._response_hooks = response_hooks

    async def _execute_with_retry(
        self, request: HttpRPCRequest, retry_config: Optional[RPCRetryPolicy]
    ) -> HttpRPCResponse:
        """Execute request with retry logic"""
        if not retry_config:
            logger.debug(
                "Executing request without retry config: %s %s",
                request.method,
                request.url,
            )
            return await self._backend.request(request)

        logger.debug(
            "Executing request with retry config: %s %s (max_attempts=%s)",
            request.method,
            request.url,
            retry_config.max_attempts,
        )
        last_exception = None
        for attempt in range(retry_config.max_attempts):
            try:
                response = await self._backend.request(request)

                # Check if we should retry based on status code
                if response.status_code in retry_config.retry_on_status_codes:
                    logger.warning(
                        "Request failed with status %s, retrying (attempt %s/%s)",
                        response.status_code,
                        attempt + 1,
                        retry_config.max_attempts,
                    )
                    if attempt < retry_config.max_attempts - 1:
                        wait_time = retry_config.backoff_factor * (2**attempt)
                        await asyncio.sleep(wait_time)
                        continue

                return response

            except Exception as e:
                last_exception = e
                logger.warning(
                    "Request failed with exception: %s, retrying (attempt %s/%s)",
                    e,
                    attempt + 1,
                    retry_config.max_attempts,
                )
                if attempt < retry_config.max_attempts - 1:
                    wait_time = retry_config.backoff_factor * (2**attempt)
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(
                        "Request failed after %s attempts: %s",
                        retry_config.max_attempts,
                        e,
                    )
                    raise

        # This should never be reached, but just in case
        raise last_exception or Exception("Retry failed")

    def build(self, cls: type[T]) -> T:
        rest_client = RestClient.get_last(cls)

        global_error_handlers = GlobalHttpErrorHandler.get(cls)

        if rest_client is None:
            raise ValueError("Class is not a rest client")

        def create_method(
            mapping: HttpMapping,
            method_call: Callable[..., Any],
            http_method: str,
            route_error_handlers: list[RouteHttpErrorHandler],
        ) -> Callable[..., Awaitable[Any]]:

            call_signature = inspect.signature(method_call)
            call_parameters = [*call_signature.parameters.keys()][1:]

            async def rpc_method(*args: Any, **kwargs: Any) -> Any:
                logger.debug(
                    "Calling RPC method %s with args=%s kwargs=%s",
                    method_call.__name__,
                    args,
                    kwargs,
                )

                args_as_kwargs = dict(zip(call_parameters, args))

                request_attributes = RequestAttribute.get(method_call)

                compiled_kwargs = {**args_as_kwargs, **kwargs}

                headers: list[tuple[str, str]] = []
                query_params = {}
                body: Any = None
                form_data: Dict[str, Any] = {}
                files: Dict[str, Any] = {}
                compiled_path = (
                    rest_client.base_path.rstrip("/") + "/" + mapping.path.lstrip("/")
                )

                # Get decorators for this method
                timeout_config = Timeout.get(method_call)
                retry_config = Retry.get(method_call)
                content_type_config = ContentType.get(method_call)

                for attr in request_attributes:
                    if attr.attribute_type == "header":
                        headers.append((attr.name, compiled_kwargs[attr.name]))
                    elif attr.attribute_type == "query":
                        query_params[attr.name] = compiled_kwargs[attr.name]
                    elif attr.attribute_type == "body":
                        body = compiled_kwargs[attr.name]
                    elif attr.attribute_type == "param":
                        compiled_path = compiled_path.replace(
                            f":{attr.name}", str(compiled_kwargs[attr.name])
                        )
                    elif attr.attribute_type == "form":
                        form_data[attr.name] = compiled_kwargs[attr.name]
                    elif attr.attribute_type == "file":
                        files[attr.name] = compiled_kwargs[attr.name]

                body_content: bytes | None = None

                # Handle different content types
                if body is not None:
                    if isinstance(body, BaseModel):
                        body_content = body.model_dump_json().encode()
                        if not content_type_config:
                            headers.append(("Content-Type", "application/json"))
                    elif isinstance(body, bytes):
                        body_content = body
                    elif isinstance(body, str):
                        body_content = body.encode()
                    elif isinstance(body, dict):
                        body_content = json.dumps(body).encode()
                        if not content_type_config:
                            headers.append(("Content-Type", "application/json"))
                    else:
                        raise ValueError(f"Invalid body type: {type(body)}")

                # Apply custom content type if specified
                if content_type_config:
                    headers.append(("Content-Type", content_type_config.content_type))

                request = HttpRPCRequest(
                    url=compiled_path,
                    method=http_method,
                    headers=headers,
                    query_params=query_params,
                    body=body_content,
                    timeout=timeout_config.seconds if timeout_config else None,
                    form_data=form_data if form_data else None,
                    files=files if files else None,
                )

                logger.debug(
                    "Prepared request: %s %s\nHeaders: %s\nQuery Params: %s\nBody: %s",
                    request.method,
                    request.url,
                    request.headers,
                    request.query_params,
                    request.body,
                )

                # Apply request hooks
                for hook in self._request_hooks:
                    request = hook.before_request(request)

                for middleware in self._middlewares:
                    request = middleware.on_request(request)

                # Check for cached response
                if hasattr(request, "_cached_response"):
                    logger.debug("Using cached response")
                    response = getattr(request, "_cached_response")
                else:
                    # Execute request with retry if configured
                    logger.debug("Executing request...")
                    response = await self._execute_with_retry(
                        request, retry_config.config if retry_config else None
                    )
                    logger.debug("Received response: status=%s", response.status_code)

                # Apply response middleware
                for response_middleware in self._response_middlewares:
                    response = response_middleware.on_response(request, response)

                # Apply response hooks
                for response_hook in self._response_hooks:
                    response = response_hook.after_response(request, response)

                # Cache response if using cache middleware and it's a GET request
                if request.method == "GET":
                    for middleware in self._middlewares:
                        if isinstance(middleware, CacheMiddleware):
                            cache_key = middleware._cache_key(request)
                            middleware.cache[cache_key] = (response, time.time())

                return_type = inspect.signature(method_call).return_annotation

                if response.status_code not in mapping.success_statuses:
                    logger.warning(
                        "Response status %s not in success statuses %s",
                        response.status_code,
                        mapping.success_statuses,
                    )
                    for error_handler in route_error_handlers + global_error_handlers:
                        if error_handler.status_code == response.status_code:
                            logger.debug(
                                "Handling error with handler for status %s",
                                response.status_code,
                            )
                            return error_handler.callback(request, response)

                    logger.error(
                        "Unhandled RPC error: %s %s",
                        response.status_code,
                        response.data,
                    )
                    raise RPCUnhandleError(request, response, None)

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

        for attr_name, method_call in inspect.getmembers(
            cls, predicate=inspect.isfunction
        ):
            if (mapping := HttpMapping.get_last(method_call)) is not None:
                route_error_handlers = RouteHttpErrorHandler.get(method_call)
                setattr(
                    dummy,
                    attr_name,
                    create_method(
                        mapping=mapping,
                        method_call=method_call,
                        http_method=mapping.method,
                        route_error_handlers=route_error_handlers,
                    ),
                )

        return cast(T, dummy)


__all__ = [
    "Post",
    "Get",
    "Patch",
    "Put",
    "Delete",
    "Query",
    "Header",
    "Body",
    "PathParam",
    "FormData",
    "File",
    "Timeout",
    "RPCRetryPolicy",
    "Retry",
    "ContentType",
    "RestClient",
    "HttpRPCAsyncBackend",
    "HttpRPCRequest",
    "HttpRPCResponse",
    "RPCRequestNetworkError",
    "RPCUnhandleError",
    "HttpRpcClientBuilder",
    "RequestMiddleware",
    "ResponseMiddleware",
    "RequestHook",
    "ResponseHook",
    "AuthenticationMiddleware",
    "BearerTokenAuth",
    "BasicAuth",
    "ApiKeyAuth",
    "CacheMiddleware",
    "TracedRequestMiddleware",
    "GlobalHttpErrorHandler",
    "RouteHttpErrorHandler",
    "HandleHttpErrorCallback",
]
