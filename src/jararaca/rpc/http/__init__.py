# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

# HTTP RPC Module - Complete REST Client Implementation
"""
This module provides a complete REST client implementation with support for:
- HTTP method decorators (@Get, @Post, @Put, @Patch, @Delete)
- Request parameter decorators (@Query, @Header, @PathParam, @Body, @FormData, @File)
- Configuration decorators (@Timeout, @Retry, @ContentType)
- Authentication middleware (BearerTokenAuth, BasicAuth, ApiKeyAuth)
- Caching and response middleware
- Request/response hooks for customization
"""

from .backends.httpx import HTTPXHttpRPCAsyncBackend
from .decorators import (  # HTTP Method decorators; Request parameter decorators; Configuration decorators; Client builder and core classes; Authentication classes; Middleware and hooks; Configuration classes; Data structures; Error handlers; Exceptions
    ApiKeyAuth,
    AuthenticationMiddleware,
    BasicAuth,
    BearerTokenAuth,
    Body,
    CacheMiddleware,
    ContentType,
    Delete,
    File,
    FormData,
    Get,
    GlobalHttpErrorHandler,
    Header,
    HttpMapping,
    HttpRpcClientBuilder,
    HttpRPCRequest,
    HttpRPCResponse,
    Patch,
    PathParam,
    Post,
    Put,
    Query,
    RequestAttribute,
    RequestHook,
    ResponseHook,
    ResponseMiddleware,
    RestClient,
    Retry,
    RouteHttpErrorHandler,
    RPCRequestNetworkError,
    RPCRetryPolicy,
    RPCUnhandleError,
    Timeout,
    TimeoutException,
)

__all__ = [
    # HTTP Method decorators
    "Get",
    "Post",
    "Put",
    "Patch",
    "Delete",
    # Request parameter decorators
    "Query",
    "Header",
    "PathParam",
    "Body",
    "FormData",
    "File",
    # Configuration decorators
    "Timeout",
    "Retry",
    "ContentType",
    # Client builder and core classes
    "RestClient",
    "HttpRpcClientBuilder",
    "HttpMapping",
    "RequestAttribute",
    # Authentication classes
    "BearerTokenAuth",
    "BasicAuth",
    "ApiKeyAuth",
    "AuthenticationMiddleware",
    # Middleware and hooks
    "CacheMiddleware",
    "ResponseMiddleware",
    "RequestHook",
    "ResponseHook",
    # Configuration classes
    "RPCRetryPolicy",
    # Data structures
    "HttpRPCRequest",
    "HttpRPCResponse",
    # Error handlers
    "GlobalHttpErrorHandler",
    "RouteHttpErrorHandler",
    # Exceptions
    "RPCRequestNetworkError",
    "RPCUnhandleError",
    "TimeoutException",
    # Backend
    "HTTPXHttpRPCAsyncBackend",
]
