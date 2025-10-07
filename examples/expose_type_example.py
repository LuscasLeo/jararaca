"""
Example demonstrating the @ExposeType decorator.

This decorator allows you to explicitly include types in TypeScript generation
without them being directly referenced in REST endpoints or WebSocket messages.
"""

from pydantic import BaseModel

from jararaca import ExposeType


# Without @ExposeType, this type would not be generated unless it's used
# in a REST endpoint request/response or as a dependency of one
@ExposeType()
class UserPermission(BaseModel):
    """A user permission that might be used in frontend logic but not in API endpoints."""

    id: str
    name: str
    description: str
    resource: str
    action: str


@ExposeType()
class SystemConfiguration(BaseModel):
    """System configuration that frontend needs to know about."""

    feature_flags: dict[str, bool]
    api_version: str
    max_upload_size: int


@ExposeType()
class ErrorCode(BaseModel):
    """Standard error codes used throughout the application."""

    code: str
    message: str
    severity: str


# You can also use it with models that have complex nested structures
@ExposeType()
class NestedComplexType(BaseModel):
    """A complex type with nested structures."""

    id: str
    permissions: list[UserPermission]
    config: SystemConfiguration
    error_codes: list[ErrorCode]


# Regular models without @ExposeType will only be generated if used in endpoints
class NotExposedType(BaseModel):
    """This will NOT appear in TypeScript output unless used in an endpoint."""

    internal_field: str
