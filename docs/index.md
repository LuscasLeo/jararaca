# Jararaca Microservice


## Overview

Jararaca is a python microservice that provides all resources for a Microservice Architecture.


## Installation

```bash
pip install jararaca
```

## Usage

```py
from app.app_config import AppConfig, AppFactoryWithAppConfig
from app.auth.auth_controller import (
    AuthConfig,
    AuthController,
    InMemoryTokenBlackListService,
    TokenBlackListService,
)
from app.extraction.models_controller import ExtractionModelController
from app.extraction.secrets_controller import SecretsController
from app.extraction.tasks_controller import TasksController
from app.providers import REDIS_TOKEN
from redis.asyncio import Redis

from jararaca import (
    AIOPikaConnectionFactory,
    AIOSQAConfig,
    AIOSqlAlchemySessionInterceptor,
    AppConfigurationInterceptor,
    MessageBusPublisherInterceptor,
    Microservice,
    ProviderSpec,
    RedisWebSocketConnectionBackend,
    Token,
    WebSocketInterceptor,
)

app = Microservice(
    providers=[
        ProviderSpec(
            provide=REDIS_TOKEN,
            use_factory=AppFactoryWithAppConfig(
                lambda config: Redis.from_url(config.REDIS_URL, decode_responses=False)
            ),
            after_interceptors=True,
        ),
        ProviderSpec(
            provide=Token(AuthConfig, "AUTH_CONFIG"),
            use_value=AuthConfig(
                secret="secret",
                identity_refresh_token_expires_delta_seconds=60 * 60 * 24 * 30,
                identity_token_expires_delta_seconds=60 * 60,
            ),
        ),
        ProviderSpec(
            provide=TokenBlackListService,
            use_value=InMemoryTokenBlackListService(),
        ),
    ],
    controllers=[
        TasksController,
        AuthController,
        SecretsController,
        ExtractionModelController,
    ],
    interceptors=[
        AppConfigurationInterceptor(
            global_configs=[
                (Token(AppConfig, "APP_CONFIG"), AppConfig),
            ]
        ),
        AppFactoryWithAppConfig(
            lambda config: MessageBusPublisherInterceptor(
                connection_factory=AIOPikaConnectionFactory(
                    url=config.AMQP_URL,
                    exchange="jararaca_ex",
                ),
            )
        ),
        AppFactoryWithAppConfig(
            lambda config: AIOSqlAlchemySessionInterceptor(
                AIOSQAConfig(
                    connection_name="default",
                    url=config.DATABASE_URL,
                )
            )
        ),
        AppFactoryWithAppConfig(
            lambda config: WebSocketInterceptor(
                backend=RedisWebSocketConnectionBackend(
                    send_pubsub_channel="jararaca:websocket:send",
                    broadcast_pubsub_channel="jararaca:websocket:broadcast",
                    conn=Redis.from_url(config.REDIS_URL, decode_responses=False),
                )
            ),
        ),
    ],
)
```
