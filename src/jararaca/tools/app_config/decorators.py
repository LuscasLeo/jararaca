from typing import Any, Type, TypeVar, cast

from pydantic import BaseModel

from jararaca.core.providers import Token

DECORATED_CLASS = TypeVar("DECORATED_CLASS", bound=Any)


class RequiresConfig:

    REQUIRE_CONFIG_ATTR = "__requires_config__"

    def __init__(self, token: Token[Any], config: Type[BaseModel]):
        self.config = config
        self.token = token

    @staticmethod
    def register(cls: Type[DECORATED_CLASS], config: "RequiresConfig") -> None:
        setattr(cls, RequiresConfig.REQUIRE_CONFIG_ATTR, config)

    @staticmethod
    def get(cls: Type[DECORATED_CLASS]) -> "RequiresConfig | None":
        if not hasattr(cls, RequiresConfig.REQUIRE_CONFIG_ATTR):
            return None

        return cast(RequiresConfig, getattr(cls, RequiresConfig.REQUIRE_CONFIG_ATTR))

    def __call__(self, cls: Type[DECORATED_CLASS]) -> Type[DECORATED_CLASS]:
        RequiresConfig.register(cls, self)
        return cls
