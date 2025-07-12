from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jararaca.broker_backend.redis_broker_backend import RedisMessageBrokerBackend
    from jararaca.messagebus.bus_message_controller import (
        ack,
        nack,
        reject,
        retry,
        retry_later,
        use_bus_message_controller,
    )
    from jararaca.microservice import AppContext, AppInterceptor, AppTransactionContext
    from jararaca.observability.interceptor import ObservabilityInterceptor
    from jararaca.observability.providers.otel import OtelObservabilityProvider
    from jararaca.persistence.sort_filter import (
        FILTER_SORT_ENTITY_ATTR_MAP,
        FilterModel,
        FilterRuleApplier,
        SortFilterRunner,
        SortModel,
        SortRuleApplier,
    )
    from jararaca.presentation.hooks import (
        raises_200_on,
        raises_400_on,
        raises_401_on,
        raises_403_on,
        raises_404_on,
        raises_422_on,
        raises_500_on,
        raises_http_exception_on,
    )
    from jararaca.reflect.controller_inspect import (
        ControllerMemberReflect,
        ControllerReflect,
    )
    from jararaca.reflect.metadata import (
        SetMetadata,
        get_all_metadata,
        get_metadata,
        get_metadata_value,
        provide_metadata,
    )
    from jararaca.rpc.http.backends.httpx import HTTPXHttpRPCAsyncBackend
    from jararaca.rpc.http.backends.otel import TracedRequestMiddleware
    from jararaca.rpc.http.decorators import (  # New request parameter decorators; Configuration decorators; Authentication classes; Middleware classes; Configuration classes; Exception classes
        ApiKeyAuth,
        AuthenticationMiddleware,
        BasicAuth,
        BearerTokenAuth,
        Body,
        CacheMiddleware,
        ContentType,
    )
    from jararaca.rpc.http.decorators import Delete as HttpDelete
    from jararaca.rpc.http.decorators import (  # New request parameter decorators; Configuration decorators; Authentication classes; Middleware classes; Configuration classes; Exception classes
        File,
        FormData,
    )
    from jararaca.rpc.http.decorators import Get as HttpGet
    from jararaca.rpc.http.decorators import (  # New request parameter decorators; Configuration decorators; Authentication classes; Middleware classes; Configuration classes; Exception classes
        GlobalHttpErrorHandler,
        Header,
        HttpMapping,
        HttpRpcClientBuilder,
    )
    from jararaca.rpc.http.decorators import Patch as HttpPatch
    from jararaca.rpc.http.decorators import (  # New request parameter decorators; Configuration decorators; Authentication classes; Middleware classes; Configuration classes; Exception classes
        PathParam,
    )
    from jararaca.rpc.http.decorators import Post as HttpPost
    from jararaca.rpc.http.decorators import Put as HttpPut
    from jararaca.rpc.http.decorators import (  # New request parameter decorators; Configuration decorators; Authentication classes; Middleware classes; Configuration classes; Exception classes
        Query,
        RequestAttribute,
        RequestHook,
        ResponseHook,
        ResponseMiddleware,
        RestClient,
        Retry,
        RetryConfig,
        RouteHttpErrorHandler,
        RPCRequestNetworkError,
        RPCUnhandleError,
        Timeout,
        TimeoutException,
    )

    from .core.providers import ProviderSpec, Token
    from .di import Container
    from .messagebus.decorators import MessageBusController, MessageHandler
    from .messagebus.interceptors.aiopika_publisher_interceptor import (
        AIOPikaConnectionFactory,
        GenericPoolConfig,
    )
    from .messagebus.interceptors.publisher_interceptor import (
        MessageBusPublisherInterceptor,
    )
    from .messagebus.message import Message, MessageOf
    from .messagebus.publisher import use_publisher
    from .microservice import (
        Microservice,
        is_shutting_down,
        request_shutdown,
        use_app_context,
        use_app_transaction_context,
        use_app_tx_ctx_data,
        use_current_container,
    )
    from .persistence.base import T_BASEMODEL, BaseEntity
    from .persistence.interceptors.aiosqa_interceptor import (
        AIOSQAConfig,
        AIOSqlAlchemySessionInterceptor,
        providing_new_session,
        providing_session,
        providing_transaction,
        use_session,
        use_transaction,
    )
    from .persistence.interceptors.decorators import (
        set_use_persistence_session,
        skip_persistence_session,
        uses_persistence_session,
    )
    from .persistence.utilities import (
        CriteriaBasedAttributeQueryInjector,
        CRUDOperations,
        DateCriteria,
        DatedEntity,
        DateOrderedFilter,
        DateOrderedQueryInjector,
        Identifiable,
        IdentifiableEntity,
        Paginated,
        PaginatedFilter,
        QueryInjector,
        QueryOperations,
        StringCriteria,
    )
    from .presentation.decorators import (
        Delete,
        Get,
        Patch,
        Post,
        Put,
        RestController,
        UseDependency,
        UseMiddleware,
    )
    from .presentation.http_microservice import HttpMicroservice, HttpMiddleware
    from .presentation.server import create_http_server
    from .presentation.websocket.context import (
        WebSocketConnectionManager,
        provide_ws_manager,
        use_ws_manager,
    )
    from .presentation.websocket.decorators import (
        RegisterWebSocketMessage,
        WebSocketEndpoint,
    )
    from .presentation.websocket.redis import RedisWebSocketConnectionBackend
    from .presentation.websocket.types import WebSocketMessage
    from .presentation.websocket.websocket_interceptor import WebSocketInterceptor
    from .scheduler.decorators import ScheduledAction
    from .tools.app_config.interceptor import AppConfigurationInterceptor
    from .tools.typescript.decorators import MutationEndpoint, QueryEndpoint

    __all__ = [
        "SetMetadata",
        "provide_metadata",
        "get_metadata",
        "get_all_metadata",
        "get_metadata_value",
        "RedisMessageBrokerBackend",
        "FilterRuleApplier",
        "SortRuleApplier",
        "use_bus_message_controller",
        "ack",
        "nack",
        "reject",
        "retry",
        "retry_later",
        "RPCRequestNetworkError",
        "RPCUnhandleError",
        "FILTER_SORT_ENTITY_ATTR_MAP",
        "FilterModel",
        "SortFilterRunner",
        "SortModel",
        "RegisterWebSocketMessage",
        "TracedRequestMiddleware",
        "raises_http_exception_on",
        "raises_200_on",
        "raises_422_on",
        "raises_404_on",
        "raises_400_on",
        "raises_401_on",
        "raises_403_on",
        "raises_500_on",
        "HttpMiddleware",
        "HttpMapping",
        "RequestAttribute",
        "Body",
        "Query",
        "Header",
        "PathParam",
        "RestClient",
        "HttpPost",
        "HttpGet",
        "HttpPatch",
        "HttpPut",
        "HttpDelete",
        "ObservabilityInterceptor",
        "QueryInjector",
        "HttpMicroservice",
        "use_current_container",
        "use_app_context",
        "use_app_transaction_context",
        "use_app_tx_ctx_data",
        "is_shutting_down",
        "request_shutdown",
        "Microservice",
        "T_BASEMODEL",
        "DatedEntity",
        "BaseEntity",
        "use_ws_manager",
        "WebSocketEndpoint",
        "CriteriaBasedAttributeQueryInjector",
        "Identifiable",
        "IdentifiableEntity",
        "MessageOf",
        "Message",
        "StringCriteria",
        "DateCriteria",
        "DateOrderedFilter",
        "DateOrderedQueryInjector",
        "Paginated",
        "PaginatedFilter",
        "QueryOperations",
        "CRUDOperations",
        "RestController",
        "MessageBusController",
        "MessageHandler",
        "ScheduledAction",
        "ProviderSpec",
        "Token",
        "AIOSqlAlchemySessionInterceptor",
        "AIOSQAConfig",
        "create_http_server",
        "Container",
        "WebSocketInterceptor",
        "use_session",
        "use_transaction",
        "providing_session",
        "provide_session",
        "uses_persistence_session",
        "skip_persistence_session",
        "set_use_persistence_session",
        "providing_transaction",
        "providing_new_session",
        "Post",
        "Get",
        "Patch",
        "Put",
        "Delete",
        "use_publisher",
        "GenericPoolConfig",
        "AIOPikaConnectionFactory",
        "MessageBusPublisherInterceptor",
        "RedisWebSocketConnectionBackend",
        "AppConfigurationInterceptor",
        "QueryEndpoint",
        "MutationEndpoint",
        "UseMiddleware",
        "UseDependency",
        "GlobalHttpErrorHandler",
        "RouteHttpErrorHandler",
        "WebSocketMessage",
        "WebSocketConnectionManager",
        "provide_ws_manager",
        "HttpRpcClientBuilder",
        "HTTPXHttpRPCAsyncBackend",
        # New request parameter decorators
        "FormData",
        "File",
        # Configuration decorators
        "Timeout",
        "Retry",
        "ContentType",
        # Authentication classes
        "BearerTokenAuth",
        "BasicAuth",
        "ApiKeyAuth",
        # Middleware classes
        "CacheMiddleware",
        "AuthenticationMiddleware",
        "ResponseMiddleware",
        "RequestHook",
        "ResponseHook",
        # Configuration classes
        "RetryConfig",
        # Exception classes
        "TimeoutException",
        "AppTransactionContext",
        "AppContext",
        "ControllerMemberReflect",
        "ControllerReflect",
        "AppInterceptor",
        "OtelObservabilityProvider",
    ]

__SPEC_PARENT__: str = __spec__.parent  # type: ignore
# A mapping of {<member name>: (package, <module name>)} defining dynamic imports
_dynamic_imports: "dict[str, tuple[str, str, str | None]]" = {
    "SetMetadata": (__SPEC_PARENT__, "reflect.metadata", None),
    "provide_metadata": (__SPEC_PARENT__, "reflect.metadata", None),
    "get_metadata": (__SPEC_PARENT__, "reflect.metadata", None),
    "get_all_metadata": (__SPEC_PARENT__, "reflect.metadata", None),
    "get_metadata_value": (__SPEC_PARENT__, "reflect.metadata", None),
    "RedisMessageBrokerBackend": (
        __SPEC_PARENT__,
        "broker_backend.redis_broker_backend",
        None,
    ),
    "FilterRuleApplier": (__SPEC_PARENT__, "persistence.sort_filter", None),
    "SortRuleApplier": (__SPEC_PARENT__, "persistence.sort_filter", None),
    "use_bus_message_controller": (
        __SPEC_PARENT__,
        "messagebus.bus_message_controller",
        None,
    ),
    "GenericPoolConfig": (
        __SPEC_PARENT__,
        "messagebus.interceptors.aiopika_publisher_interceptor",
        None,
    ),
    "ack": (__SPEC_PARENT__, "messagebus.bus_message_controller", None),
    "nack": (__SPEC_PARENT__, "messagebus.bus_message_controller", None),
    "reject": (__SPEC_PARENT__, "messagebus.bus_message_controller", None),
    "retry": (__SPEC_PARENT__, "messagebus.bus_message_controller", None),
    "retry_later": (__SPEC_PARENT__, "messagebus.bus_message_controller", None),
    "RPCRequestNetworkError": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "RPCUnhandleError": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "FILTER_SORT_ENTITY_ATTR_MAP": (__SPEC_PARENT__, "persistence.sort_filter", None),
    "FilterModel": (__SPEC_PARENT__, "persistence.sort_filter", None),
    "SortFilterRunner": (__SPEC_PARENT__, "persistence.sort_filter", None),
    "SortModel": (__SPEC_PARENT__, "persistence.sort_filter", None),
    "RegisterWebSocketMessage": (
        __SPEC_PARENT__,
        "presentation.websocket.decorators",
        None,
    ),
    "OtelObservabilityProvider": (
        __SPEC_PARENT__,
        "observability.providers.otel",
        None,
    ),
    "TracedRequestMiddleware": (__SPEC_PARENT__, "rpc.http.backends.otel", None),
    "raises_http_exception_on": (__SPEC_PARENT__, "presentation.hooks", None),
    "raises_200_on": (__SPEC_PARENT__, "presentation.hooks", None),
    "raises_400_on": (__SPEC_PARENT__, "presentation.hooks", None),
    "raises_401_on": (__SPEC_PARENT__, "presentation.hooks", None),
    "raises_403_on": (__SPEC_PARENT__, "presentation.hooks", None),
    "raises_404_on": (__SPEC_PARENT__, "presentation.hooks", None),
    "raises_422_on": (__SPEC_PARENT__, "presentation.hooks", None),
    "raises_500_on": (__SPEC_PARENT__, "presentation.hooks", None),
    "HttpMiddleware": (__SPEC_PARENT__, "presentation.http_microservice", None),
    "HttpMapping": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "RequestAttribute": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "Body": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "Query": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "Header": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "PathParam": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "RestClient": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "HttpPost": (__SPEC_PARENT__, "rpc.http.decorators", "Post"),
    "HttpGet": (__SPEC_PARENT__, "rpc.http.decorators", "Get"),
    "HttpPatch": (__SPEC_PARENT__, "rpc.http.decorators", "Patch"),
    "HttpPut": (__SPEC_PARENT__, "rpc.http.decorators", "Put"),
    "HttpDelete": (__SPEC_PARENT__, "rpc.http.decorators", "Delete"),
    "ObservabilityInterceptor": (__SPEC_PARENT__, "observability.interceptor", None),
    "QueryInjector": (__SPEC_PARENT__, "persistence.utilities", None),
    "HttpMicroservice": (__SPEC_PARENT__, "presentation.http_microservice", None),
    "use_current_container": (__SPEC_PARENT__, "microservice", None),
    "T_BASEMODEL": (__SPEC_PARENT__, "persistence.base", None),
    "DatedEntity": (__SPEC_PARENT__, "persistence.utilities", None),
    "BaseEntity": (__SPEC_PARENT__, "persistence.base", None),
    "use_ws_manager": (__SPEC_PARENT__, "presentation.websocket.context", None),
    "WebSocketEndpoint": (__SPEC_PARENT__, "presentation.websocket.decorators", None),
    "CriteriaBasedAttributeQueryInjector": (
        __SPEC_PARENT__,
        "persistence.utilities",
        None,
    ),
    "Identifiable": (__SPEC_PARENT__, "persistence.utilities", None),
    "IdentifiableEntity": (__SPEC_PARENT__, "persistence.utilities", None),
    "MessageOf": (__SPEC_PARENT__, "messagebus.message", None),
    "Message": (__SPEC_PARENT__, "messagebus.message", None),
    "StringCriteria": (__SPEC_PARENT__, "persistence.utilities", None),
    "DateCriteria": (__SPEC_PARENT__, "persistence.utilities", None),
    "DateOrderedFilter": (__SPEC_PARENT__, "persistence.utilities", None),
    "DateOrderedQueryInjector": (__SPEC_PARENT__, "persistence.utilities", None),
    "Paginated": (__SPEC_PARENT__, "persistence.utilities", None),
    "PaginatedFilter": (__SPEC_PARENT__, "persistence.utilities", None),
    "QueryOperations": (__SPEC_PARENT__, "persistence.utilities", None),
    "CRUDOperations": (__SPEC_PARENT__, "persistence.utilities", None),
    "RestController": (__SPEC_PARENT__, "presentation.decorators", None),
    "MessageBusController": (__SPEC_PARENT__, "messagebus.decorators", None),
    "MessageHandler": (__SPEC_PARENT__, "messagebus.decorators", None),
    "ScheduledAction": (__SPEC_PARENT__, "scheduler.decorators", None),
    "Microservice": (__SPEC_PARENT__, "microservice", None),
    "ProviderSpec": (__SPEC_PARENT__, "core.providers", None),
    "Token": (__SPEC_PARENT__, "core.providers", None),
    "AIOSqlAlchemySessionInterceptor": (
        __SPEC_PARENT__,
        "persistence.interceptors.aiosqa_interceptor",
        None,
    ),
    "AIOSQAConfig": (
        __SPEC_PARENT__,
        "persistence.interceptors.aiosqa_interceptor",
        None,
    ),
    "create_http_server": (__SPEC_PARENT__, "presentation.server", None),
    "Container": (__SPEC_PARENT__, "di", None),
    "WebSocketInterceptor": (
        __SPEC_PARENT__,
        "presentation.websocket.websocket_interceptor",
        None,
    ),
    "use_session": (
        __SPEC_PARENT__,
        "persistence.interceptors.aiosqa_interceptor",
        None,
    ),
    "use_transaction": (
        __SPEC_PARENT__,
        "persistence.interceptors.aiosqa_interceptor",
        None,
    ),
    "providing_session": (
        __SPEC_PARENT__,
        "persistence.interceptors.aiosqa_interceptor",
        None,
    ),
    "provide_session": (
        __SPEC_PARENT__,
        "persistence.interceptors.aiosqa_interceptor",
        None,
    ),
    "providing_new_session": (
        __SPEC_PARENT__,
        "persistence.interceptors.aiosqa_interceptor",
        None,
    ),
    "providing_transaction": (
        __SPEC_PARENT__,
        "persistence.interceptors.aiosqa_interceptor",
        None,
    ),
    "uses_persistence_session": (
        __SPEC_PARENT__,
        "persistence.interceptors.decorators",
        None,
    ),
    "skip_persistence_session": (
        __SPEC_PARENT__,
        "persistence.interceptors.decorators",
        None,
    ),
    "set_use_persistence_session": (
        __SPEC_PARENT__,
        "persistence.interceptors.decorators",
        None,
    ),
    "Post": (__SPEC_PARENT__, "presentation.decorators", None),
    "Get": (__SPEC_PARENT__, "presentation.decorators", None),
    "Patch": (__SPEC_PARENT__, "presentation.decorators", None),
    "Put": (__SPEC_PARENT__, "presentation.decorators", None),
    "Delete": (__SPEC_PARENT__, "presentation.decorators", None),
    "use_publisher": (__SPEC_PARENT__, "messagebus.publisher", None),
    "AIOPikaConnectionFactory": (
        __SPEC_PARENT__,
        "messagebus.interceptors.aiopika_publisher_interceptor",
        None,
    ),
    "MessageBusPublisherInterceptor": (
        __SPEC_PARENT__,
        "messagebus.interceptors.publisher_interceptor",
        None,
    ),
    "RedisWebSocketConnectionBackend": (
        __SPEC_PARENT__,
        "presentation.websocket.redis",
        None,
    ),
    "AppConfigurationInterceptor": (
        __SPEC_PARENT__,
        "tools.app_config.interceptor",
        None,
    ),
    "QueryEndpoint": (__SPEC_PARENT__, "tools.typescript.decorators", None),
    "MutationEndpoint": (__SPEC_PARENT__, "tools.typescript.decorators", None),
    "UseMiddleware": (__SPEC_PARENT__, "presentation.decorators", None),
    "UseDependency": (__SPEC_PARENT__, "presentation.decorators", None),
    "GlobalHttpErrorHandler": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "RouteHttpErrorHandler": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "WebSocketMessage": (__SPEC_PARENT__, "presentation.websocket.types", None),
    "WebSocketConnectionManager": (
        __SPEC_PARENT__,
        "presentation.websocket.context",
        None,
    ),
    "provide_ws_manager": (__SPEC_PARENT__, "presentation.websocket.context", None),
    "HttpRpcClientBuilder": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "HTTPXHttpRPCAsyncBackend": (__SPEC_PARENT__, "rpc.http.backends.httpx", None),
    # New HTTP RPC classes
    "FormData": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "File": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "Timeout": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "Retry": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "ContentType": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "BearerTokenAuth": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "BasicAuth": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "ApiKeyAuth": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "CacheMiddleware": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "AuthenticationMiddleware": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "ResponseMiddleware": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "RequestHook": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "ResponseHook": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "RetryConfig": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "TimeoutException": (__SPEC_PARENT__, "rpc.http.decorators", None),
    "use_app_context": (__SPEC_PARENT__, "microservice", None),
    "use_app_transaction_context": (__SPEC_PARENT__, "microservice", None),
    "use_app_tx_ctx_data": (__SPEC_PARENT__, "microservice", None),
    "is_shutting_down": (__SPEC_PARENT__, "microservice", None),
    "request_shutdown": (__SPEC_PARENT__, "microservice", None),
    "AppContext": (__SPEC_PARENT__, "microservice", None),
    "AppInterceptor": (__SPEC_PARENT__, "microservice", None),
    "AppTransactionContext": (__SPEC_PARENT__, "microservice", None),
    "ControllerMemberReflect": (__SPEC_PARENT__, "reflect.controller_inspect", None),
    "ControllerReflect": (__SPEC_PARENT__, "reflect.controller_inspect", None),
}


def __getattr__(attr_name: str) -> object:

    dynamic_attr = _dynamic_imports.get(attr_name)
    if dynamic_attr is None:
        raise AttributeError(f"module {__name__!r} has no attribute {attr_name!r}")

    package, module_name, realname = dynamic_attr

    if module_name == "__module__":
        result = import_module(f".{attr_name}", package=package)
        globals()[attr_name] = result
        return result
    else:
        module = import_module(f"{package}.{module_name}", package=package)
        result = getattr(module, attr_name if realname is None else realname)
        g = globals()
        g[attr_name] = result
        for k, (_, _, realname) in _dynamic_imports.items():
            # if v_module_name == module_name and k not in _deprecated_dynamic_imports:
            g[k] = getattr(module, k if realname is None else realname)
        return result


def __dir__() -> "list[str]":
    return list(__all__)
