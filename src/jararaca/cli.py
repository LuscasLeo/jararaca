import importlib

import click
import uvicorn

from jararaca.messagebus.worker import create_messagebus_worker
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
def worker(app_path: str) -> None:

    app = find_app_by_module_path(app_path)

    create_messagebus_worker(app)


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
