import logging

from examples.client import HelloRPC
from examples.controller import MyController
from jararaca import Microservice, ProviderSpec, Token
from jararaca.observability.interceptor import (
    ObservabilityInterceptor,
    OtelObservabilityProvider,
)
from jararaca.presentation.http_microservice import HttpMicroservice
from jararaca.presentation.server import create_http_server
from jararaca.rpc.http.decorators import (
    HttpRpcClientBuilder,
    HTTPXHttpRPCAsyncBackend,
    TracedRequestMiddleware,
)

logger = logging.getLogger(__name__)


app = Microservice(
    providers=[
        ProviderSpec(
            provide=Token(HelloRPC, "HELLO_RPC"),
            use_value=HttpRpcClientBuilder(
                HTTPXHttpRPCAsyncBackend(prefix_url="http://localhost:8000"),
                middlewares=[TracedRequestMiddleware()],
            ).build(
                HelloRPC  # type: ignore[type-abstract]
            ),
        )
    ],
    controllers=[MyController],
    interceptors=[
        ObservabilityInterceptor(
            OtelObservabilityProvider.from_url("App-example", "http://localhost:4318")
        )
    ],
)


http_app = create_http_server(HttpMicroservice(app))


loggers = logging.root.manager.loggerDict.get("examples")

if isinstance(loggers, logging.PlaceHolder):

    for logger_name in loggers.loggerMap.keys():
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger_name.addHandler(stream_handler)
        logger_name.setLevel(logging.DEBUG)
