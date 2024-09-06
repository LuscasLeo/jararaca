import importlib
from urllib.parse import urlparse, urlunsplit

import click
import uvicorn

from jararaca.messagebus.worker import AioPikaWorkerConfig, create_messagebus_worker
from jararaca.microservice import Microservice
from jararaca.presentation.server import create_http_server


def find_app_by_module_path(
    module_path: str,
) -> Microservice:
    if ":" not in module_path:
        raise ValueError("'%s' is not a valid module path" % module_path)

    module_name, app = module_path.rsplit(":", 1)

    try:
        module = importlib.import_module(module_name)
    except ImportError:
        raise ValueError("App module not found")

    if not hasattr(module, app):
        raise ValueError("module %s has no attribute %s" % (module, app))

    app = getattr(module, app)

    if not isinstance(app, Microservice):
        raise ValueError("App must be an instance of App")

    return app


@click.group()
def cli() -> None:
    pass


@cli.command()
@click.argument(
    "app_path",
    type=str,
)
@click.option(
    "--url",
    type=str,
    default="amqp://guest:guest@localhost/",
)
@click.option(
    "--username",
    type=str,
    default=None,
)
@click.option(
    "--password",
    type=str,
    default=None,
)
@click.option(
    "--exchange",
    type=str,
    default="jararaca_ex",
)
@click.option(
    "--queue",
    type=str,
    default="jararaca_q",
)
@click.option(
    "--prefetch-count",
    type=int,
    default=100,
)
def worker(
    app_path: str,
    url: str,
    username: str | None,
    password: str | None,
    exchange: str,
    queue: str,
    prefetch_count: int,
) -> None:

    app = find_app_by_module_path(app_path)

    parsed_url = urlparse(url)

    if password is not None:
        parsed_url = urlparse(
            urlunsplit(
                parsed_url._replace(
                    netloc=f"{parsed_url.username or ''}:{password}@{parsed_url.netloc}"
                )
            )
        )

    if username is not None:
        parsed_url = urlparse(
            urlunsplit(
                parsed_url._replace(
                    netloc=f"{username}{':%s' % password if password is not None else ''}@{parsed_url.netloc}"
                )
            )
        )

    url = parsed_url.geturl()

    config = AioPikaWorkerConfig(
        url=url,
        exchange=exchange,
        queue=queue,
        prefetch_count=prefetch_count,
    )

    create_messagebus_worker(app, config=config)


@cli.command()
@click.argument(
    "app_path",
    type=str,
)
@click.option(
    "--host",
    type=str,
    default="0.0.0.0",
)
@click.option(
    "--port",
    type=int,
    default=8000,
)
def server(app_path: str, host: str, port: int) -> None:

    app = find_app_by_module_path(app_path)

    asgi_app = create_http_server(app)

    uvicorn.run(asgi_app, host=host, port=port)
