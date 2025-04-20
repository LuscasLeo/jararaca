import importlib
import importlib.resources
import os
import sys
import time
from codecs import StreamWriter
from typing import Any
from urllib.parse import urlparse, urlunsplit

import click
import uvicorn
from mako.template import Template  # type: ignore

from jararaca.messagebus.worker import AioPikaWorkerConfig, MessageBusWorker
from jararaca.microservice import Microservice
from jararaca.presentation.http_microservice import HttpMicroservice
from jararaca.presentation.server import create_http_server
from jararaca.scheduler.scheduler import Scheduler, SchedulerBackend, SchedulerConfig
from jararaca.tools.typescript.interface_parser import (
    write_microservice_to_typescript_interface,
)

LIBRARY_FILES_PATH = importlib.resources.files("jararaca.files")
ENTITY_TEMPLATE_PATH = LIBRARY_FILES_PATH / "entity.py.mako"


def find_item_by_module_path(
    module_path: str,
) -> Any:
    if ":" not in module_path:
        raise ValueError("'%s' is not a valid module path" % module_path)

    module_name, app = module_path.rsplit(":", 1)

    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError("App module not found") from e

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
@click.option(
    "--watch",
    is_flag=True,
    help="Watch for file changes and regenerate TypeScript interfaces",
)
@click.option(
    "--src-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default="src",
    help="Source directory to watch for changes (default: src)",
)
def gen_tsi(app_path: str, file_path: StreamWriter, watch: bool, src_dir: str) -> None:
    """Generate TypeScript interfaces from a Python microservice."""

    # Generate typescript interfaces
    def generate_interfaces() -> None:
        try:
            app = find_microservice_by_module_path(app_path)
            content = write_microservice_to_typescript_interface(app)

            # Save current position
            file_path.tell()

            # Reset file to beginning
            file_path.seek(0)
            file_path.truncate()

            # Write new content
            file_path.write(content)
            file_path.flush()

            print(f"Generated TypeScript interfaces at {time.strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"Error generating TypeScript interfaces: {e}", file=sys.stderr)

    # Initial generation
    generate_interfaces()

    # If watch mode is not enabled, exit
    if not watch:
        return

    try:
        from watchdog.events import FileSystemEvent, FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print(
            "Watchdog is required for watch mode. Install it with: pip install watchdog",
            file=sys.stderr,
        )
        return

    # Set up file system event handler
    class PyFileChangeHandler(FileSystemEventHandler):
        def on_modified(self, event: FileSystemEvent) -> None:
            src_path = (
                event.src_path
                if isinstance(event.src_path, str)
                else str(event.src_path)
            )
            if not event.is_directory and src_path.endswith(".py"):
                print(f"File changed: {src_path}")
                generate_interfaces()

    # Set up observer
    observer = Observer()
    observer.schedule(PyFileChangeHandler(), src_dir, recursive=True)
    observer.start()

    print(f"Watching for changes in {os.path.abspath(src_dir)}...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("Watch mode stopped")
    observer.join()


def camel_case_to_snake_case(name: str) -> str:
    return "".join(["_" + c.lower() if c.isupper() else c for c in name]).lstrip("_")


def camel_case_to_kebab_case(name: str) -> str:
    return "".join(["-" + c.lower() if c.isupper() else c for c in name]).lstrip("-")


def camel_case_to_pascal_case(name: str) -> str:
    return name[0].upper() + name[1:]


@cli.command()
@click.argument("entity_name", type=click.STRING)
@click.argument(
    "file_path",
    type=click.File("w"),
)
def gen_entity(entity_name: str, file_path: StreamWriter) -> None:

    template = Template(filename=str(ENTITY_TEMPLATE_PATH))

    entity_snake_case = camel_case_to_snake_case(entity_name)
    entity_pascal_case = camel_case_to_pascal_case(entity_name)
    entity_kebab_case = camel_case_to_kebab_case(entity_name)

    file_path.write(
        template.render(
            entityNameSnakeCase=entity_snake_case,
            entityNamePascalCase=entity_pascal_case,
            entityNameKebabCase=entity_kebab_case,
        )
    )
