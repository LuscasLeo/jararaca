import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Protocol, Sequence, Tuple, Type

from pydantic import BaseModel

from jararaca.core.providers import Token
from jararaca.microservice import (
    AppInterceptor,
    AppInterceptorWithLifecycle,
    AppTransactionContext,
    Container,
    Microservice,
)
from jararaca.tools.app_config.decorators import RequiresConfig

logger = logging.getLogger(__name__)


class ConfigParser(Protocol):

    def __call__(self, cls: Type[BaseModel]) -> BaseModel: ...


class FromEnvConfigParser(ConfigParser):

    def __call__(self, cls: Type[BaseModel]) -> BaseModel:
        return cls.model_validate({**os.environ})


class AppConfigurationInterceptor(AppInterceptor, AppInterceptorWithLifecycle):

    def __init__(
        self,
        global_configs: Sequence[Tuple[Token[Any], Type[BaseModel]]] = [],
        config_parser: ConfigParser = FromEnvConfigParser(),
    ):
        self.global_configs = global_configs
        self.config_parser = config_parser

    @asynccontextmanager
    async def intercept(
        self, app_context: AppTransactionContext
    ) -> AsyncGenerator[None, None]:
        yield

    def instance_basemodels(self, basemodel_type: Type[BaseModel]) -> BaseModel:
        return self.config_parser(basemodel_type)

    def get_configs_from_application(
        self, app: Microservice, container: Container
    ) -> Sequence[Tuple[Token[Any], Type[BaseModel]]]:
        return [
            *self.global_configs,
            *[
                (config.token, config.config)
                for controller in app.controllers
                if (config := RequiresConfig.get(controller))
            ],
        ]

    @asynccontextmanager
    async def lifecycle(
        self,
        app: Microservice,
        container: Container,
    ) -> AsyncGenerator[None, None]:

        configs = [
            *self.global_configs,
            *self.get_configs_from_application(app, container),
        ]

        errors: list[str] = []

        for config in configs:
            token, basemodel_type = config

            try:
                basemodel_instance = self.instance_basemodels(basemodel_type)
            except Exception as e:
                errors.append(f"Error creating {basemodel_type}: {e}")
                continue

            container.register(basemodel_instance, token)

        if errors:
            raise AppConfigValidationError("\n".join(errors))

        yield

        logger.info("finalizando")


class AppConfigValidationError(Exception):
    pass
