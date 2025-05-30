import asyncio
import importlib
import importlib.resources
import multiprocessing
import os
import sys
import time
from codecs import StreamWriter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunsplit

import aio_pika
import click
import uvicorn
from mako.template import Template

from jararaca.messagebus import worker as worker_v1
from jararaca.messagebus import worker_v2 as worker_v2_mod
from jararaca.messagebus.decorators import MessageBusController, MessageHandler
from jararaca.microservice import Microservice
from jararaca.presentation.http_microservice import HttpMicroservice
from jararaca.presentation.server import create_http_server
from jararaca.reflect.controller_inspect import inspect_controller
from jararaca.scheduler.decorators import ScheduledAction
from jararaca.scheduler.scheduler import Scheduler
from jararaca.scheduler.scheduler_v2 import SchedulerV2
from jararaca.tools.typescript.interface_parser import (
    write_microservice_to_typescript_interface,
)
from jararaca.utils.rabbitmq_utils import RabbitmqUtils

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
        if e.name == module_name:
            raise ImportError("Module not found") from e
        else:
            raise

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


# The v1 infrastructure declaration function has been removed as part of the CLI simplification


async def declare_worker_infrastructure(
    broker_url: str,
    app: Microservice,
    force: bool = False,
) -> None:
    """
    Declare the infrastructure (exchanges and queues) for worker.
    """

    parsed_url = urlparse(broker_url)
    if parsed_url.scheme not in ["amqp", "amqps"]:
        raise ValueError(f"Unsupported broker URL scheme: {parsed_url.scheme}")

    if not parsed_url.query:
        raise ValueError("Query string must be set for AMQP URLs")

    query_params = parse_qs(parsed_url.query)

    if "exchange" not in query_params or not query_params["exchange"]:
        raise ValueError("Exchange must be set in the query string")

    exchange = query_params["exchange"][0]

    connection = await aio_pika.connect(broker_url)
    channel = await connection.channel()

    # Force delete infrastructure if requested
    if force:
        click.echo(f"→ Force deleting existing infrastructure for exchange: {exchange}")
        await RabbitmqUtils.delete_exchange(channel, exchange)
        await RabbitmqUtils.delete_exchange(channel, RabbitmqUtils.DEAD_LETTER_EXCHANGE)
        await RabbitmqUtils.delete_queue(channel, RabbitmqUtils.DEAD_LETTER_QUEUE)

    await RabbitmqUtils.declare_main_exchange(
        channel=channel,
        exchange_name=exchange,
        passive=not force,  # If force is True, we already deleted the exchange
    )

    dlx = await RabbitmqUtils.declare_dl_exchange(channel=channel, passive=not force)
    dlq = await RabbitmqUtils.declare_dl_queue(channel=channel, passive=not force)
    await dlq.bind(dlx, routing_key=RabbitmqUtils.DEAD_LETTER_EXCHANGE)

    # Find all message handlers and scheduled actions

    for instance_type in app.controllers:
        controller_spec = MessageBusController.get_messagebus(instance_type)
        if controller_spec is None:
            continue

        _, members = inspect_controller(instance_type)

        # Declare queues for message handlers
        for name, member in members.items():

            message_handler = MessageHandler.get_message_incoming(
                member.member_function
            )
            if message_handler is not None:

                queue_name = f"{message_handler.message_type.MESSAGE_TOPIC}.{member.member_function.__module__}.{member.member_function.__qualname__}"
                routing_key = f"{message_handler.message_type.MESSAGE_TOPIC}.#"

                # Force delete queue if requested
                if force:
                    await RabbitmqUtils.delete_queue(channel, queue_name)

                # Declare queue
                queue = await RabbitmqUtils.declare_queue(
                    channel=channel, queue_name=queue_name, passive=not force
                )
                await queue.bind(exchange=exchange, routing_key=routing_key)
                click.echo(
                    f"✓ Declared message handler queue: {queue_name} (routing key: {routing_key})"
                )

            scheduled_action = ScheduledAction.get_scheduled_action(
                member.member_function
            )
            if scheduled_action is not None:

                # Declare queues for scheduled actions

                queue_name = f"{member.member_function.__module__}.{member.member_function.__qualname__}"
                routing_key = queue_name

                # Force delete queue if requested
                if force:
                    await RabbitmqUtils.delete_queue(channel, queue_name)

                queue = await RabbitmqUtils.declare_queue(
                    channel=channel, queue_name=queue_name, passive=not force
                )
                await queue.bind(exchange=exchange, routing_key=routing_key)
                click.echo(
                    f"✓ Declared scheduled action queue: {queue_name} (routing key: {routing_key})"
                )

    await channel.close()
    await connection.close()


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
    envvar="BROKER_URL",
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
    "--prefetch-count",
    type=int,
    default=1,
)
@click.option(
    "--passive-declare",
    is_flag=True,
    default=False,
    help="[DEPRECATED] Use passive declarations (check if infrastructure exists without creating it)",
)
@click.option(
    "--handlers",
    type=str,
    help="Comma-separated list of handler names to listen to. If not specified, all handlers will be used.",
)
def worker(
    app_path: str,
    url: str,
    username: str | None,
    password: str | None,
    exchange: str,
    prefetch_count: int,
    handlers: str | None,
    passive_declare: bool,
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

    # Parse handler names if provided
    handler_names: set[str] | None = None
    if handlers:
        handler_names = {name.strip() for name in handlers.split(",") if name.strip()}

    config = worker_v1.AioPikaWorkerConfig(
        url=url,
        exchange=exchange,
        prefetch_count=prefetch_count,
    )

    worker_v1.MessageBusWorker(app, config=config).start_sync(
        handler_names=handler_names,
    )


@cli.command()
@click.argument(
    "app_path",
    type=str,
    envvar="APP_PATH",
)
@click.option(
    "--broker-url",
    type=str,
    envvar="BROKER_URL",
)
@click.option(
    "--backend-url",
    type=str,
    envvar="BACKEND_URL",
)
@click.option(
    "--handlers",
    type=str,
    help="Comma-separated list of handler names to listen to. If not specified, all handlers will be used.",
)
def worker_v2(
    app_path: str, broker_url: str, backend_url: str, handlers: str | None
) -> None:

    app = find_microservice_by_module_path(app_path)

    # Parse handler names if provided
    handler_names: set[str] | None = None
    if handlers:
        handler_names = {name.strip() for name in handlers.split(",") if name.strip()}

    worker_v2_mod.MessageBusWorker(
        app=app,
        broker_url=broker_url,
        backend_url=backend_url,
        handler_names=handler_names,
    ).start_sync()


@cli.command()
@click.argument(
    "app_path",
    type=str,
    envvar="APP_PATH",
)
@click.option(
    "--host",
    type=str,
    default="0.0.0.0",
    envvar="HOST",
)
@click.option(
    "--port",
    type=int,
    default=8000,
    envvar="PORT",
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
@click.option(
    "--schedulers",
    type=str,
    help="Comma-separated list of scheduler names to run (only run schedulers with these names)",
)
def scheduler(
    app_path: str,
    interval: int,
    schedulers: str | None = None,
) -> None:
    app = find_microservice_by_module_path(app_path)

    # Parse scheduler names if provided
    scheduler_names: set[str] | None = None
    if schedulers:
        scheduler_names = {
            name.strip() for name in schedulers.split(",") if name.strip()
        }

    Scheduler(app, interval=interval, scheduler_names=scheduler_names).run()


@cli.command()
@click.argument(
    "app_path",
    type=str,
)
@click.option(
    "--interval",
    type=int,
    default=1,
    required=True,
)
@click.option(
    "--broker-url",
    type=str,
    required=True,
)
@click.option(
    "--backend-url",
    type=str,
    required=True,
)
@click.option(
    "--schedulers",
    type=str,
    help="Comma-separated list of scheduler names to run (only run schedulers with these names)",
)
def scheduler_v2(
    interval: int,
    broker_url: str,
    backend_url: str,
    app_path: str,
    schedulers: str | None = None,
) -> None:

    app = find_microservice_by_module_path(app_path)

    # Parse scheduler names if provided
    scheduler_names: set[str] | None = None
    if schedulers:
        scheduler_names = {
            name.strip() for name in schedulers.split(",") if name.strip()
        }

    scheduler = SchedulerV2(
        app=app,
        interval=interval,
        backend_url=backend_url,
        broker_url=broker_url,
        scheduler_names=scheduler_names,
    )
    scheduler.run()


def generate_interfaces(
    app_path: str,
    file_path: str | None = None,
    stdout: bool = False,
    post_process_cmd: str | None = None,
) -> str:
    try:
        app = find_microservice_by_module_path(app_path)
        content = write_microservice_to_typescript_interface(app)

        if stdout:
            return content

        if not file_path:
            return content

        with open(file_path, "w", encoding="utf-8") as file:
            # Save current position
            file.tell()

            # Reset file to beginning
            file.seek(0)
            file.truncate()

            # Write new content
            file.write(content)
            file.flush()

        click.echo(
            f"Generated TypeScript interfaces at {time.strftime('%H:%M:%S')} at {str(Path(file_path).absolute())}"
        )

        if post_process_cmd and file_path:
            import subprocess

            try:
                click.echo(f"Running post-process command: {post_process_cmd}")
                subprocess.run(
                    post_process_cmd.replace("{file}", file_path),
                    shell=True,
                    check=True,
                )
                click.echo(f"Post-processing completed successfully")
            except subprocess.CalledProcessError as e:
                click.echo(f"Post-processing command failed: {e}", file=sys.stderr)

        return content
    except Exception as e:
        click.echo(f"Error generating TypeScript interfaces: {e}", file=sys.stderr)
        return ""


@cli.command()
@click.argument(
    "app_path",
    type=str,
)
@click.argument(
    "file_path",
    type=click.Path(file_okay=True, dir_okay=False),
    required=False,
)
@click.option(
    "--watch",
    is_flag=True,
)
@click.option(
    "--src-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default="src",
)
@click.option(
    "--stdout",
    is_flag=True,
    help="Print generated interfaces to stdout instead of writing to a file",
)
@click.option(
    "--post-process",
    type=str,
    help="Command to run after generating the interfaces, {file} will be replaced with the output file path",
)
def gen_tsi(
    app_path: str,
    file_path: str | None,
    watch: bool,
    src_dir: str,
    stdout: bool,
    post_process: str | None,
) -> None:
    """Generate TypeScript interfaces from a Python microservice."""

    if stdout and watch:
        click.echo(
            "Error: --watch and --stdout options cannot be used together",
            file=sys.stderr,
        )
        return

    if not file_path and not stdout:
        click.echo(
            "Error: either file_path or --stdout must be provided", file=sys.stderr
        )
        return

    if post_process and stdout:
        click.echo(
            "Error: --post-process and --stdout options cannot be used together",
            file=sys.stderr,
        )
        return

    # Initial generation
    content = generate_interfaces(app_path, file_path, stdout, post_process)

    if stdout:
        click.echo(content)
        return

    # If watch mode is not enabled, exit
    if not watch:
        return

    try:
        from watchdog.events import FileSystemEvent, FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        click.echo(
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
                click.echo(f"File changed: {src_path}")
                # Create a completely detached process to ensure classes are reloaded
                process = multiprocessing.get_context("spawn").Process(
                    target=generate_interfaces,
                    args=(app_path, file_path, False, post_process),
                    daemon=False,  # Non-daemon to ensure it completes
                )
                process.start()
                # Don't join to keep it detached from main process

        def _run_generator_in_separate_process(
            self, app_path: str, file_path: str
        ) -> None:
            # Using Python executable to start a completely new process
            # This ensures all modules are freshly imported
            generate_interfaces(app_path, file_path)
            # cmd = [
            #     sys.executable,
            #     "-c",
            #     (
            #         f"import sys; sys.path.extend({sys.path}); "
            #         f"from jararaca.cli import generate_interfaces; "
            #         f"generate_interfaces('{app_path}', '{file_path}')"
            #     ),
            # ]
            # import subprocess

            # subprocess.run(cmd, check=False)

    # Set up observer
    observer = Observer()
    observer.schedule(PyFileChangeHandler(), src_dir, recursive=True)  # type: ignore
    observer.start()  # type: ignore

    click.echo(f"Watching for changes in {os.path.abspath(src_dir)}...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()  # type: ignore
        click.echo("Watch mode stopped")
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


@cli.command()
@click.argument(
    "app_path",
    type=str,
)
@click.option(
    "--broker-url",
    type=str,
    envvar="BROKER_URL",
    help="Broker URL (e.g., amqp://guest:guest@localhost/) [env: BROKER_URL]",
)
@click.option(
    "--exchange",
    type=str,
    default="jararaca_ex",
    envvar="EXCHANGE",
    help="Exchange name [env: EXCHANGE]",
)
@click.option(
    "--passive-declare",
    is_flag=True,
    default=False,
    help="Use passive declarations (check if infrastructure exists without creating it)",
)
@click.option(
    "--handlers",
    type=str,
    help="Comma-separated list of handler names to declare queues for. If not specified, all handlers will be declared.",
)
@click.option(
    "--schedulers",
    type=str,
    help="Comma-separated list of scheduler names to declare queues for. If not specified, all schedulers will be declared.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Force recreation by deleting existing exchanges and queues before declaring them",
)
def declare(
    app_path: str,
    broker_url: str | None,
    exchange: str,
    passive_declare: bool,
    handlers: str | None,
    schedulers: str | None,
    force: bool,
) -> None:
    """
    Declare RabbitMQ infrastructure (exchanges and queues) for message handlers and schedulers.

    This command pre-declares the necessary exchanges and queues for message handlers and schedulers,
    without starting the actual consumption processes.

    Environment variables:
    - BROKER_URL: Broker URL (e.g., amqp://guest:guest@localhost/)
    - EXCHANGE: Exchange name (defaults to 'jararaca_ex')

    Examples:

    \b
    # Declare infrastructure
    jararaca declare myapp:app --broker-url amqp://guest:guest@localhost/

    \b
    # Force recreation of queues and exchanges
    jararaca declare myapp:app --broker-url amqp://guest:guest@localhost/ --force

    \b
    # Use environment variables
    export BROKER_URL="amqp://guest:guest@localhost/"
    export EXCHANGE="my_exchange"
    jararaca declare myapp:app
    """

    app = find_microservice_by_module_path(app_path)

    async def run_declarations() -> None:
        if not broker_url:
            click.echo(
                "ERROR: --broker-url is required or set BROKER_URL environment variable",
                err=True,
            )
            return

        # Parse handler names if provided
        handler_names: set[str] | None = None
        if handlers:
            handler_names = {
                name.strip() for name in handlers.split(",") if name.strip()
            }

        # Parse scheduler names if provided
        scheduler_names: set[str] | None = None
        if schedulers:
            scheduler_names = {
                name.strip() for name in schedulers.split(",") if name.strip()
            }

        try:
            # Create the broker URL with exchange parameter
            broker_url_with_exchange = f"{broker_url}?exchange={exchange}"

            click.echo(
                f"→ Declaring worker infrastructure (URL: {broker_url_with_exchange})"
            )
            click.echo(
                f"→ Declaring scheduler infrastructure (URL: {broker_url_with_exchange})"
            )

            await declare_worker_infrastructure(broker_url_with_exchange, app, force)

            click.echo("✓ Worker and scheduler infrastructure declared successfully!")
        except Exception as e:
            click.echo(f"ERROR: Failed to declare infrastructure: {e}", err=True)
            raise

    asyncio.run(run_declarations())
