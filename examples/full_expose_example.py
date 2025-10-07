"""
Complete example showing @ExposeType in a real microservice context.

This demonstrates how to use @ExposeType to expose types that are not
directly used in REST endpoints but are needed on the frontend.
"""

from pydantic import BaseModel

from jararaca import ExposeType, Get, Microservice, RestController, SplitInputOutput


# These types will be generated even though they're not in any endpoint
@ExposeType()
class NotificationPreference(BaseModel):
    """User notification preferences - used in frontend state management."""

    email_enabled: bool
    push_enabled: bool
    sms_enabled: bool
    frequency: str  # "immediate", "daily", "weekly"


@ExposeType()
class ThemeSettings(BaseModel):
    """UI theme configuration - frontend only."""

    dark_mode: bool
    primary_color: str
    accent_color: str
    font_size: str  # "small", "medium", "large"


@ExposeType()
class UserPermissionSet(BaseModel):
    """Permission set - used for frontend authorization logic."""

    can_read: bool
    can_write: bool
    can_delete: bool
    can_admin: bool
    resource_type: str


# This type is both exposed explicitly AND used in an endpoint
@ExposeType()
@SplitInputOutput()
class UserProfile(BaseModel):
    """User profile - generates Input/Output and always available."""

    id: str
    username: str
    email: str
    display_name: str | None = None
    avatar_url: str | None = None


# Regular endpoint response - only generated because used in endpoint
class UserListResponse(BaseModel):
    users: list[UserProfile]
    total: int
    page: int
    page_size: int


@RestController("/api/users")
class UserController:
    @Get("/")
    async def list_users(self, page: int = 1, page_size: int = 10) -> UserListResponse:
        """
        This endpoint will generate:
        - UserListResponse interface
        - UserProfileInput interface (from @ExposeType + @SplitInputOutput)
        - UserProfileOutput interface (from @ExposeType + @SplitInputOutput)

        Additionally, these are available even without being in endpoints:
        - NotificationPreference interface
        - ThemeSettings interface
        - UserPermissionSet interface
        """
        return UserListResponse(
            users=[],
            total=0,
            page=page,
            page_size=page_size,
        )


# Create the microservice
app = Microservice(
    providers=[],
    controllers=[UserController],
    interceptors=[],
)


if __name__ == "__main__":
    print("Available exposed types:")
    for t in sorted(ExposeType.get_all_exposed_types(), key=lambda x: x.__name__):
        print(f"  - {t.__name__}")
    print("\nTo generate TypeScript interfaces:")
    print("  jararaca gen-tsi examples.full_expose_example:app output.ts")
