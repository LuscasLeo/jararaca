import importlib
from codecs import StreamWriter
from typing import Any
from urllib.parse import urlparse, urlunsplit

import click
import uvicorn

from jararaca.messagebus.worker import AioPikaWorkerConfig, MessageBusWorker
from jararaca.microservice import Microservice
from jararaca.presentation.http_microservice import HttpMicroservice
from jararaca.presentation.server import create_http_server
from jararaca.scheduler.scheduler import Scheduler, SchedulerBackend, SchedulerConfig
from jararaca.tools.typescript.interface_parser import (
    write_microservice_to_typescript_interface,
)


def find_item_by_module_path(
    module_path: str,
) -> Any:
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

    return app


def find_microservice_by_module_path(module_path: str) -> Microservice:

    app = find_item_by_module_path(module_path)

    if not isinstance(app, Microservice):
        raise ValueError(
            (
                "%s must be an instance of Microservice (it is %s)"
                % (app, str(type(app)))
            )
        )

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
    default=1,
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

    app = find_microservice_by_module_path(app_path)

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

    MessageBusWorker(app, config=config).start_sync()


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

    item = find_item_by_module_path(app_path)

    if isinstance(item, Microservice):
        app = HttpMicroservice(item)
    elif isinstance(item, HttpMicroservice):
        app = item
    else:
        raise ValueError(
            (
                "%s must be an instance of Microservice or HttpMicroservice (it is %s)"
                % (item, str(type(item)))
            )
        )

    asgi_app = create_http_server(app)

    uvicorn.run(asgi_app, host=host, port=port)


class NullBackend(SchedulerBackend): ...


@cli.command()
@click.argument(
    "app_path",
    type=str,
)
@click.option(
    "--interval",
    type=int,
    default=1,
)
def scheduler(
    app_path: str,
    interval: int,
) -> None:
    app = find_microservice_by_module_path(app_path)

    Scheduler(app, NullBackend(), SchedulerConfig(interval=interval)).run()


@cli.command()
@click.argument(
    "app_path",
    type=str,
)
@click.argument(
    "file_path",
    type=click.File("w"),
)
def gen_tsi(app_path: str, file_path: StreamWriter) -> None:
    app = find_microservice_by_module_path(app_path)

    content = write_microservice_to_typescript_interface(app)

    file_path.write(content)
