# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import importlib
import importlib.resources
import multiprocessing
import os
import sys
import time
import traceback
import typing
from codecs import StreamWriter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import parse_qs, urlparse

import aio_pika
import click
import uvicorn
from mako.template import Template

from jararaca.messagebus import worker as worker_mod
from jararaca.messagebus.decorators import MessageBusController, MessageHandler
from jararaca.microservice import Microservice
from jararaca.presentation.http_microservice import HttpMicroservice
from jararaca.presentation.server import create_http_server
from jararaca.reflect.controller_inspect import (
    ControllerMemberReflect,
    inspect_controller,
)
from jararaca.scheduler.beat_worker import BeatWorker
from jararaca.scheduler.decorators import ScheduledAction
from jararaca.tools.typescript.interface_parser import (
    write_microservice_to_typescript_interface,
)
from jararaca.utils.rabbitmq_utils import RabbitmqUtils

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver

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
    interactive_mode: bool = False,
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

    # Create a connection that will be used to create channels for each operation
    connection = await aio_pika.connect(broker_url)

    try:
        # Step 1: Setup infrastructure (exchanges and dead letter queues)
        # Creating a dedicated channel for infrastructure setup
        await setup_infrastructure(connection, exchange, force, interactive_mode)

        # Step 2: Declare all message handlers and scheduled actions queues
        # Creating a dedicated channel for controller queues
        await declare_controller_queues(
            connection, app, exchange, force, interactive_mode
        )
    finally:
        # Always close the connection in the finally block
        await connection.close()


async def setup_infrastructure(
    connection: aio_pika.abc.AbstractConnection,
    exchange: str,
    force: bool = False,
    interactive_mode: bool = False,
) -> None:
    """
    Setup the basic infrastructure (exchanges and dead letter queues).
    """
    # Check if infrastructure exists
    infrastructure_exists = await check_infrastructure_exists(connection, exchange)

    # If it exists and force or user confirms, delete it
    if not infrastructure_exists and should_recreate_infrastructure(
        force,
        interactive_mode,
        f"Existing infrastructure found for exchange '{exchange}'. Delete and recreate?",
    ):
        await delete_infrastructure(connection, exchange)

    # Try to declare required infrastructure
    await declare_infrastructure(connection, exchange, force, interactive_mode)


async def check_infrastructure_exists(
    connection: aio_pika.abc.AbstractConnection, exchange: str
) -> bool:
    """
    Check if the infrastructure exists by trying passive declarations.
    """
    # Create a dedicated channel for checking infrastructure existence using async context manager
    async with connection.channel() as channel:
        try:
            await RabbitmqUtils.declare_main_exchange(
                channel=channel,
                exchange_name=exchange,
                passive=True,
            )
            await RabbitmqUtils.declare_dl_exchange(channel=channel, passive=True)
            await RabbitmqUtils.declare_dl_queue(channel=channel, passive=True)
            return True
        except Exception:
            # Infrastructure doesn't exist, which is fine for fresh setup
            return False


def should_recreate_infrastructure(
    force: bool, interactive_mode: bool, confirmation_message: str
) -> bool:
    """
    Determine if infrastructure should be recreated based on force flag and user input.
    """
    return force or (interactive_mode and click.confirm(confirmation_message))


async def delete_infrastructure(
    connection: aio_pika.abc.AbstractConnection, exchange: str
) -> None:
    """
    Delete existing infrastructure (exchanges and queues).
    """
    # Create a dedicated channel for deleting infrastructure using async context manager
    async with connection.channel() as channel:
        click.echo(f"→ Deleting existing infrastructure for exchange: {exchange}")
        await RabbitmqUtils.delete_exchange(channel, exchange)
        await RabbitmqUtils.delete_exchange(channel, RabbitmqUtils.DEAD_LETTER_EXCHANGE)
        await RabbitmqUtils.delete_queue(channel, RabbitmqUtils.DEAD_LETTER_QUEUE)


async def declare_infrastructure(
    connection: aio_pika.abc.AbstractConnection,
    exchange: str,
    force: bool,
    interactive_mode: bool,
) -> None:
    """
    Declare the required infrastructure (exchanges and dead letter queues).
    """
    # Using async context manager for channel creation
    async with connection.channel() as channel:
        try:
            # Declare main exchange
            await RabbitmqUtils.declare_main_exchange(
                channel=channel,
                exchange_name=exchange,
                passive=False,
            )

            # Declare dead letter exchange and queue
            dlx = await RabbitmqUtils.declare_dl_exchange(
                channel=channel, passive=False
            )
            dlq = await RabbitmqUtils.declare_dl_queue(channel=channel, passive=False)
            await dlq.bind(dlx, routing_key=RabbitmqUtils.DEAD_LETTER_EXCHANGE)

        except Exception as e:
            click.echo(f"Error during exchange declaration: {e}")

            # If interactive mode and user confirms, or if forced, try again after deletion
            if should_recreate_infrastructure(
                force, interactive_mode, "Delete existing infrastructure and recreate?"
            ):
                # Delete infrastructure with a new channel
                await delete_infrastructure(connection, exchange)

                # Try again with a new channel
                async with connection.channel() as new_channel:
                    # Try again after deletion
                    await RabbitmqUtils.declare_main_exchange(
                        channel=new_channel,
                        exchange_name=exchange,
                        passive=False,
                    )
                    dlx = await RabbitmqUtils.declare_dl_exchange(
                        channel=new_channel, passive=False
                    )
                    dlq = await RabbitmqUtils.declare_dl_queue(
                        channel=new_channel, passive=False
                    )
                    await dlq.bind(dlx, routing_key=RabbitmqUtils.DEAD_LETTER_EXCHANGE)
            elif force:
                # If force is true but recreation failed, propagate the error
                raise
            else:
                click.echo("Skipping main exchange declaration due to error")


async def declare_controller_queues(
    connection: aio_pika.abc.AbstractConnection,
    app: Microservice,
    exchange: str,
    force: bool,
    interactive_mode: bool,
) -> None:
    """
    Declare all message handler and scheduled action queues for controllers.
    """
    for instance_type in app.controllers:
        controller_spec = MessageBusController.get_last(instance_type)
        if controller_spec is None:
            continue

        _, members = inspect_controller(instance_type)

        # Process each member (method) in the controller
        for _, member in members.items():
            # Check if it's a message handler
            await declare_message_handler_queue(
                connection, member, exchange, force, interactive_mode, controller_spec
            )

            # Check if it's a scheduled action
            await declare_scheduled_action_queue(
                connection, member, exchange, force, interactive_mode
            )


async def declare_message_handler_queue(
    connection: aio_pika.abc.AbstractConnection,
    member: ControllerMemberReflect,
    exchange: str,
    force: bool,
    interactive_mode: bool,
    controller_spec: MessageBusController,
) -> None:
    """
    Declare a queue for a message handler if the member is one.
    """
    message_handler = MessageHandler.get_last(member.member_function)
    if message_handler is not None:
        queue_name = f"{message_handler.message_type.MESSAGE_TOPIC}.{member.member_function.__module__}.{member.member_function.__qualname__}"
        routing_key = f"{message_handler.message_type.MESSAGE_TOPIC}.#"

        await declare_and_bind_queue(
            connection,
            queue_name,
            routing_key,
            exchange,
            force,
            interactive_mode,
            is_scheduled_action=False,
        )


async def declare_scheduled_action_queue(
    connection: aio_pika.abc.AbstractConnection,
    member: ControllerMemberReflect,
    exchange: str,
    force: bool,
    interactive_mode: bool,
) -> None:
    """
    Declare a queue for a scheduled action if the member is one.
    """
    scheduled_action = ScheduledAction.get_last(member.member_function)
    if scheduled_action is not None:
        queue_name = (
            f"{member.member_function.__module__}.{member.member_function.__qualname__}"
        )
        routing_key = queue_name

        await declare_and_bind_queue(
            connection,
            queue_name,
            routing_key,
            exchange,
            force,
            interactive_mode,
            is_scheduled_action=True,
        )


async def declare_and_bind_queue(
    connection: aio_pika.abc.AbstractConnection,
    queue_name: str,
    routing_key: str,
    exchange: str,
    force: bool,
    interactive_mode: bool,
    is_scheduled_action: bool,
) -> None:
    """
    Declare and bind a queue to the exchange with the given routing key.
    """
    queue_type = "scheduled action" if is_scheduled_action else "message handler"

    # Using async context manager for channel creation
    async with connection.channel() as channel:
        try:
            # Try to declare queue using the appropriate method
            if is_scheduled_action:
                queue = await RabbitmqUtils.declare_scheduled_action_queue(
                    channel=channel, queue_name=queue_name, passive=False
                )
            else:
                queue = await RabbitmqUtils.declare_worker_queue(
                    channel=channel, queue_name=queue_name, passive=False
                )

            # Bind the queue to the exchange
            await queue.bind(exchange=exchange, routing_key=routing_key)
            click.echo(
                f"✓ Declared {queue_type} queue: {queue_name} (routing key: {routing_key})"
            )
        except Exception as e:
            click.echo(f"⚠ Error declaring {queue_type} queue {queue_name}: {e}")

            # If interactive mode and user confirms, or if forced, recreate the queue
            if force or (
                interactive_mode
                and click.confirm(f"Delete and recreate queue {queue_name}?")
            ):
                # Create a new channel for deletion and recreation
                async with connection.channel() as new_channel:
                    # Delete the queue
                    await RabbitmqUtils.delete_queue(new_channel, queue_name)

                    # Try to declare queue again using the appropriate method
                    if is_scheduled_action:
                        queue = await RabbitmqUtils.declare_scheduled_action_queue(
                            channel=new_channel, queue_name=queue_name, passive=False
                        )
                    else:
                        queue = await RabbitmqUtils.declare_worker_queue(
                            channel=new_channel, queue_name=queue_name, passive=False
                        )

                    # Bind the queue to the exchange
                    await queue.bind(exchange=exchange, routing_key=routing_key)
                    click.echo(
                        f"✓ Recreated {queue_type} queue: {queue_name} (routing key: {routing_key})"
                    )
            else:
                click.echo(f"⚠ Skipping {queue_type} queue {queue_name}")


@click.group()
def cli() -> None:
    pass


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
    required=True,
    help="The URL for the message broker",
)
@click.option(
    "--backend-url",
    type=str,
    envvar="BACKEND_URL",
    required=True,
    help="The URL for the message broker backend",
)
@click.option(
    "--handlers",
    type=str,
    envvar="HANDLERS",
    help="Comma-separated list of handler names to listen to. If not specified, all handlers will be used.",
)
@click.option(
    "--reload",
    is_flag=True,
    envvar="RELOAD",
    help="Enable auto-reload when Python files change.",
)
@click.option(
    "--src-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default="src",
    envvar="SRC_DIR",
    help="The source directory to watch for changes when --reload is enabled.",
)
@click.option(
    "--gracious-shutdown-seconds",
    type=int,
    default=20,
    envvar="GRACIOUS_SHUTDOWN_SECONDS",
    help="Number of seconds to wait for graceful shutdown on reload",
)
def worker(
    app_path: str,
    broker_url: str,
    backend_url: str,
    handlers: str | None,
    reload: bool,
    src_dir: str,
    gracious_shutdown_seconds: int,
) -> None:
    """Start a message bus worker that processes asynchronous messages from a message queue."""

    if reload:
        process_args = {
            "app_path": app_path,
            "broker_url": broker_url,
            "backend_url": backend_url,
            "handlers": handlers,
        }
        run_with_reload_watcher(
            process_args, run_worker_process, src_dir, gracious_shutdown_seconds
        )
    else:
        run_worker_process(app_path, broker_url, backend_url, handlers)


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
    envvar="APP_PATH",
)
@click.option(
    "--interval",
    type=int,
    default=1,
    required=True,
    envvar="INTERVAL",
)
@click.option(
    "--broker-url",
    type=str,
    required=True,
    envvar="BROKER_URL",
)
@click.option(
    "--backend-url",
    type=str,
    required=True,
    envvar="BACKEND_URL",
)
@click.option(
    "--actions",
    type=str,
    envvar="ACTIONS",
    help="Comma-separated list of action names to run (only run actions with these names)",
)
@click.option(
    "--reload",
    is_flag=True,
    envvar="RELOAD",
    help="Enable auto-reload when Python files change.",
)
@click.option(
    "--src-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default="src",
    envvar="SRC_DIR",
    help="The source directory to watch for changes when --reload is enabled.",
)
@click.option(
    "--gracious-shutdown-seconds",
    type=int,
    default=20,
    envvar="GRACIOUS_SHUTDOWN_SECONDS",
    help="Number of seconds to wait for graceful shutdown on reload",
)
def beat(
    interval: int,
    broker_url: str,
    backend_url: str,
    app_path: str,
    actions: str | None = None,
    reload: bool = False,
    src_dir: str = "src",
    gracious_shutdown_seconds: int = 20,
) -> None:
    """Start a scheduler that dispatches scheduled actions to workers."""

    if reload:
        process_args = {
            "app_path": app_path,
            "interval": interval,
            "broker_url": broker_url,
            "backend_url": backend_url,
            "actions": actions,
        }
        run_with_reload_watcher(
            process_args, run_beat_process, src_dir, gracious_shutdown_seconds
        )
    else:
        run_beat_process(app_path, interval, broker_url, backend_url, actions)


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
        traceback.print_exc(file=sys.stderr)
        return ""


@cli.command()
@click.argument(
    "app_path",
    type=str,
    envvar="APP_PATH",
)
@click.argument(
    "file_path",
    type=click.Path(file_okay=True, dir_okay=False),
    required=False,
    envvar="FILE_PATH",
)
@click.option(
    "--watch",
    is_flag=True,
    envvar="WATCH",
)
@click.option(
    "--src-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default="src",
    envvar="SRC_DIR",
)
@click.option(
    "--stdout",
    is_flag=True,
    envvar="STDOUT",
    help="Print generated interfaces to stdout instead of writing to a file",
)
@click.option(
    "--post-process",
    type=str,
    envvar="POST_PROCESS",
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

    @typing.no_type_check
    def start_watchdog() -> None:

        observer: "BaseObserver" = Observer()
        observer.schedule(PyFileChangeHandler(), src_dir, recursive=True)
        observer.start()

        click.echo(f"Watching for changes in {os.path.abspath(src_dir)}...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
            click.echo("Watch mode stopped")
        observer.join()

    start_watchdog()


def camel_case_to_snake_case(name: str) -> str:
    return "".join(["_" + c.lower() if c.isupper() else c for c in name]).lstrip("_")


def camel_case_to_kebab_case(name: str) -> str:
    return "".join(["-" + c.lower() if c.isupper() else c for c in name]).lstrip("-")


def camel_case_to_pascal_case(name: str) -> str:
    return name[0].upper() + name[1:]


@cli.command()
@click.argument("entity_name", type=click.STRING, envvar="ENTITY_NAME")
@click.argument(
    "file_path",
    type=click.File("w"),
    envvar="FILE_PATH",
)
def gen_entity(entity_name: str, file_path: StreamWriter) -> None:

    template = Template(filename=str(ENTITY_TEMPLATE_PATH))

    entity_snake_case = camel_case_to_snake_case(entity_name)
    entity_pascal_case = camel_case_to_pascal_case(entity_name)
    entity_kebab_case = camel_case_to_kebab_case(entity_name)

    file_path.write(
        str(
            template.render(
                entityNameSnakeCase=entity_snake_case,
                entityNamePascalCase=entity_pascal_case,
                entityNameKebabCase=entity_kebab_case,
            )
        )
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
    help="Broker URL (e.g., amqp://guest:guest@localhost/) [env: BROKER_URL]",
)
@click.option(
    "-i",
    "--interactive-mode",
    is_flag=True,
    default=False,
    envvar="INTERACTIVE_MODE",
    help="Enable interactive mode for queue declaration (confirm before deleting existing queues)",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    default=False,
    envvar="FORCE",
    help="Force recreation by deleting existing exchanges and queues before declaring them",
)
def declare(
    app_path: str,
    broker_url: str,
    force: bool,
    interactive_mode: bool,
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

        try:
            # Create the broker URL with exchange parameter

            click.echo(f"→ Declaring worker infrastructure (URL: {broker_url})")
            await declare_worker_infrastructure(
                broker_url, app, force, interactive_mode
            )

            click.echo("✓ Workers infrastructure declared successfully!")
        except Exception as e:
            click.echo(f"ERROR: Failed to declare infrastructure: {e}", err=True)
            raise

    asyncio.run(run_declarations())


def run_worker_process(
    app_path: str, broker_url: str, backend_url: str, handlers: str | None
) -> None:
    """Run a worker process with the given parameters."""
    app = find_microservice_by_module_path(app_path)

    # Parse handler names if provided
    handler_names: set[str] | None = None
    if handlers:
        handler_names = {name.strip() for name in handlers.split(",") if name.strip()}

    click.echo(f"Starting worker for {app_path}...")
    worker_mod.MessageBusWorker(
        app=app,
        broker_url=broker_url,
        backend_url=backend_url,
        handler_names=handler_names,
    ).start_sync()


def run_beat_process(
    app_path: str, interval: int, broker_url: str, backend_url: str, actions: str | None
) -> None:
    """Run a beat scheduler process with the given parameters."""
    app = find_microservice_by_module_path(app_path)

    # Parse scheduler names if provided
    scheduler_names: set[str] | None = None
    if actions:
        scheduler_names = {name.strip() for name in actions.split(",") if name.strip()}

    click.echo(f"Starting beat scheduler for {app_path}...")
    beat_worker = BeatWorker(
        app=app,
        interval=interval,
        backend_url=backend_url,
        broker_url=broker_url,
        scheduled_action_names=scheduler_names,
    )
    beat_worker.run()


def run_with_reload_watcher(
    process_args: dict[str, Any],
    process_target: Callable[..., Any],
    src_dir: str = "src",
    max_graceful_shutdown_seconds: int = 20,
) -> None:
    """
    Run a process with a file watcher that will restart it when Python files change.

    Args:
        process_args: Arguments to pass to the process function
        process_target: The function to run as the process
        src_dir: The directory to watch for changes
    """
    try:
        from watchdog.events import FileSystemEvent, FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        click.echo(
            "Watchdog is required for reload mode. Install it with: pip install watchdog",
            file=sys.stderr,
        )
        return

    # Run the initial process
    process = multiprocessing.get_context("spawn").Process(
        target=process_target,
        kwargs=process_args,
        daemon=False,  # Non-daemon to ensure it completes properly
    )
    process.start()  # Set up file system event handler

    class PyFileChangeHandler(FileSystemEventHandler):
        def __init__(self) -> None:
            self.last_modified_time = time.time()
            self.debounce_seconds = 1.0  # Debounce to avoid multiple restarts
            self.active_process = process

        def on_modified(self, event: FileSystemEvent) -> None:
            src_path = (
                event.src_path
                if isinstance(event.src_path, str)
                else str(event.src_path)
            )

            # Ignore non-Python files and directories
            if event.is_directory or not src_path.endswith(".py"):
                return

            # Debounce to avoid multiple restarts
            current_time = time.time()
            if current_time - self.last_modified_time < self.debounce_seconds:
                return
            self.last_modified_time = current_time

            click.echo(f"Detected change in {src_path}")
            click.echo("Restarting process...")

            # Terminate the current process
            if self.active_process and self.active_process.is_alive():
                self.active_process.terminate()
                self.active_process.join(timeout=max_graceful_shutdown_seconds)

                # If process doesn't terminate, kill it
                if self.active_process.is_alive():
                    click.echo("Process did not terminate gracefully, killing it")
                    self.active_process.kill()
                    self.active_process.join()

            # Create a new process

            self.active_process = multiprocessing.get_context("spawn").Process(
                target=process_target,
                kwargs=process_args,
                daemon=False,
            )
            self.active_process.start()

    @typing.no_type_check
    def start_watchdog() -> None:

        # Set up observer
        observer = Observer()
        observer.schedule(PyFileChangeHandler(), src_dir, recursive=True)
        observer.start()

        click.echo(f"Watching for changes in {os.path.abspath(src_dir)}...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
            if process.is_alive():
                click.echo("Stopping process...")
                process.terminate()
                process.join(timeout=max_graceful_shutdown_seconds)
                if process.is_alive():
                    process.kill()
                    process.join()
            click.echo("Reload mode stopped")
        observer.join()

    start_watchdog()


# =============================================================================
# Dead Letter Queue (DLQ) Commands
# =============================================================================


@dataclass
class DLQMessage:
    """Represents a message in the Dead Letter Queue."""

    body: bytes
    routing_key: str
    original_queue: str
    death_reason: str
    death_count: int
    first_death_time: str
    message_id: str | None
    content_type: str | None


async def fetch_dlq_messages(
    connection: aio_pika.abc.AbstractConnection,
    limit: int | None = None,
    consume: bool = False,
) -> list[tuple[DLQMessage, aio_pika.abc.AbstractIncomingMessage]]:
    """
    Fetch messages from the Dead Letter Queue.

    Args:
        connection: The AMQP connection
        limit: Maximum number of messages to fetch (None for all)
        consume: If True, messages are consumed (acked), otherwise they are requeued

    Returns:
        List of DLQMessage objects with their raw messages
    """
    messages: list[tuple[DLQMessage, aio_pika.abc.AbstractIncomingMessage]] = []

    async with connection.channel() as channel:
        try:
            queue = await RabbitmqUtils.get_dl_queue(channel)

            count = 0
            while True:
                if limit is not None and count >= limit:
                    break

                try:
                    raw_message = await asyncio.wait_for(
                        queue.get(no_ack=False), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    break

                if raw_message is None:
                    break

                # Extract x-death header information
                headers = raw_message.headers or {}
                x_death_raw = headers.get("x-death")

                original_queue = ""
                death_reason = ""
                death_count = 0
                first_death_time = ""

                # x-death is a list of dicts when messages are dead-lettered
                if isinstance(x_death_raw, list) and len(x_death_raw) > 0:
                    death_info = x_death_raw[0]
                    if isinstance(death_info, dict):
                        original_queue = str(death_info.get("queue", "unknown"))
                        death_reason = str(death_info.get("reason", "unknown"))
                        count_val = death_info.get("count", 1)
                        if isinstance(count_val, (int, float)):
                            death_count = int(count_val)
                        else:
                            death_count = 0
                        first_death_time_raw = death_info.get("time")
                        if first_death_time_raw:
                            first_death_time = str(first_death_time_raw)

                dlq_message = DLQMessage(
                    body=raw_message.body,
                    routing_key=raw_message.routing_key or "",
                    original_queue=original_queue,
                    death_reason=death_reason,
                    death_count=death_count,
                    first_death_time=first_death_time,
                    message_id=raw_message.message_id,
                    content_type=raw_message.content_type,
                )

                messages.append((dlq_message, raw_message))

                if not consume:
                    # Requeue the message so it stays in the DLQ
                    await raw_message.nack(requeue=True)
                count += 1

        except Exception as e:
            click.echo(f"Error fetching DLQ messages: {e}", err=True)
            raise

    return messages


async def get_dlq_stats_by_queue(
    connection: aio_pika.abc.AbstractConnection,
) -> dict[str, dict[str, Any]]:
    """
    Get DLQ statistics grouped by original queue.

    Returns:
        Dictionary with queue names as keys and stats as values
    """
    messages = await fetch_dlq_messages(connection, consume=False)

    stats: dict[str, dict[str, Any]] = {}

    for dlq_message, _ in messages:
        queue_name = dlq_message.original_queue or "unknown"

        if queue_name not in stats:
            stats[queue_name] = {
                "count": 0,
                "reasons": {},
                "oldest_death": None,
                "newest_death": None,
            }

        stats[queue_name]["count"] += 1

        # Track death reasons
        reason = dlq_message.death_reason or "unknown"
        if reason not in stats[queue_name]["reasons"]:
            stats[queue_name]["reasons"][reason] = 0
        stats[queue_name]["reasons"][reason] += 1

        # Track oldest/newest death times
        if dlq_message.first_death_time:
            death_time = dlq_message.first_death_time
            if (
                stats[queue_name]["oldest_death"] is None
                or death_time < stats[queue_name]["oldest_death"]
            ):
                stats[queue_name]["oldest_death"] = death_time
            if (
                stats[queue_name]["newest_death"] is None
                or death_time > stats[queue_name]["newest_death"]
            ):
                stats[queue_name]["newest_death"] = death_time

    return stats


@cli.group()
def dlq() -> None:
    """Dead Letter Queue (DLQ) management commands.

    Commands for inspecting, managing, and recovering messages from the
    Dead Letter Queue.
    """


@dlq.command("stats")
@click.option(
    "--broker-url",
    type=str,
    envvar="BROKER_URL",
    required=True,
    help="The URL for the message broker",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output statistics in JSON format",
)
def dlq_stats(broker_url: str, output_json: bool) -> None:
    """Show statistics about the Dead Letter Queue.

    Displays the total message count and a breakdown by original queue,
    including death reasons and timestamps.

    Examples:

    \b
    # Show DLQ stats
    jararaca dlq stats --broker-url amqp://guest:guest@localhost/

    \b
    # Output as JSON
    jararaca dlq stats --broker-url amqp://guest:guest@localhost/ --json
    """

    async def run_stats() -> None:
        connection = await aio_pika.connect(broker_url)
        try:
            # Get total message count
            async with connection.channel() as channel:
                try:
                    queue_info = await channel.declare_queue(
                        RabbitmqUtils.DEAD_LETTER_QUEUE, passive=True
                    )
                    total_count = queue_info.declaration_result.message_count or 0
                except Exception:
                    click.echo("Dead Letter Queue does not exist or is not accessible.")
                    return

            if total_count == 0:
                if output_json:
                    import json

                    click.echo(
                        json.dumps({"total_messages": 0, "queues": {}}, indent=2)
                    )
                else:
                    click.echo("✓ Dead Letter Queue is empty!")
                return

            # Get detailed stats by queue
            stats = await get_dlq_stats_by_queue(connection)

            if output_json:
                import json

                result = {"total_messages": total_count, "queues": stats}
                click.echo(json.dumps(result, indent=2, default=str))
            else:
                click.echo(f"\n{'='*60}")
                click.echo("Dead Letter Queue Statistics")
                click.echo(f"{'='*60}")
                click.echo(f"\nTotal Messages: {total_count}")
                click.echo("\nBreakdown by Original Queue:")
                click.echo(f"{'-'*60}")

                for queue_name, queue_stats in sorted(
                    stats.items(), key=lambda x: x[1]["count"], reverse=True
                ):
                    click.echo(f"\n  📦 {queue_name}")
                    click.echo(f"     Messages: {queue_stats['count']}")
                    click.echo("     Reasons: ")
                    for reason, count in queue_stats["reasons"].items():
                        click.echo(f"       - {reason}: {count}")
                    if queue_stats["oldest_death"]:
                        click.echo(f"     Oldest: {queue_stats['oldest_death']}")
                    if queue_stats["newest_death"]:
                        click.echo(f"     Newest: {queue_stats['newest_death']}")

                click.echo(f"\n{'='*60}")

        finally:
            await connection.close()

    asyncio.run(run_stats())


@dlq.command("list")
@click.option(
    "--broker-url",
    type=str,
    envvar="BROKER_URL",
    required=True,
    help="The URL for the message broker",
)
@click.option(
    "--limit",
    type=int,
    default=10,
    help="Maximum number of messages to display (default: 10)",
)
@click.option(
    "--queue",
    "queue_filter",
    type=str,
    default=None,
    help="Filter messages by original queue name (supports partial match)",
)
@click.option(
    "--show-body",
    is_flag=True,
    default=False,
    help="Show message body content",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output messages in JSON format",
)
def dlq_list(
    broker_url: str,
    limit: int,
    queue_filter: str | None,
    show_body: bool,
    output_json: bool,
) -> None:
    """List messages in the Dead Letter Queue.

    Shows details about each message including the original queue,
    death reason, and timestamps.

    Examples:

    \b
    # List first 10 messages
    jararaca dlq list --broker-url amqp://guest:guest@localhost/

    \b
    # List messages from a specific queue
    jararaca dlq list --broker-url amqp://guest:guest@localhost/ --queue user.events

    \b
    # Show message bodies
    jararaca dlq list --broker-url amqp://guest:guest@localhost/ --show-body
    """

    async def run_list() -> None:
        connection = await aio_pika.connect(broker_url)
        try:
            messages = await fetch_dlq_messages(connection, limit=limit, consume=False)

            if not messages:
                click.echo("No messages in the Dead Letter Queue.")
                return

            # Filter by queue if specified
            if queue_filter:
                messages = [
                    (msg, raw)
                    for msg, raw in messages
                    if queue_filter.lower() in msg.original_queue.lower()
                ]

            if not messages:
                click.echo(f"No messages found matching queue filter: '{queue_filter}'")
                return

            if output_json:
                import json

                result = []
                for dlq_msg, _ in messages:
                    msg_dict: dict[str, Any] = {
                        "message_id": dlq_msg.message_id,
                        "original_queue": dlq_msg.original_queue,
                        "routing_key": dlq_msg.routing_key,
                        "death_reason": dlq_msg.death_reason,
                        "death_count": dlq_msg.death_count,
                        "first_death_time": dlq_msg.first_death_time,
                        "content_type": dlq_msg.content_type,
                    }
                    if show_body:
                        try:
                            msg_dict["body"] = dlq_msg.body.decode("utf-8")
                        except Exception:
                            msg_dict["body"] = dlq_msg.body.hex()
                    result.append(msg_dict)
                click.echo(json.dumps(result, indent=2, default=str))
            else:
                click.echo(f"\n{'='*70}")
                click.echo(f"Dead Letter Queue Messages (showing {len(messages)})")
                click.echo(f"{'='*70}")

                for i, (dlq_msg, _) in enumerate(messages, 1):
                    click.echo(f"\n[{i}] Message ID: {dlq_msg.message_id or 'N/A'}")
                    click.echo(f"    Original Queue: {dlq_msg.original_queue}")
                    click.echo(f"    Routing Key: {dlq_msg.routing_key}")
                    click.echo(f"    Death Reason: {dlq_msg.death_reason}")
                    click.echo(f"    Death Count: {dlq_msg.death_count}")
                    click.echo(f"    First Death: {dlq_msg.first_death_time or 'N/A'}")
                    click.echo(f"    Content-Type: {dlq_msg.content_type or 'N/A'}")

                    if show_body:
                        try:
                            body_str = dlq_msg.body.decode("utf-8")
                            # Truncate if too long
                            if len(body_str) > 500:
                                body_str = body_str[:500] + "... (truncated)"
                            click.echo(f"    Body: {body_str}")
                        except Exception:
                            click.echo(f"    Body (hex): {dlq_msg.body[:100].hex()}...")

                click.echo(f"\n{'='*70}")

        finally:
            await connection.close()

    asyncio.run(run_list())


@dlq.command("purge")
@click.option(
    "--broker-url",
    type=str,
    envvar="BROKER_URL",
    required=True,
    help="The URL for the message broker",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt",
)
def dlq_purge(broker_url: str, force: bool) -> None:
    """Purge all messages from the Dead Letter Queue.

    WARNING: This action is irreversible. All messages will be permanently deleted.

    Examples:

    \b
    # Purge with confirmation
    jararaca dlq purge --broker-url amqp://guest:guest@localhost/

    \b
    # Purge without confirmation
    jararaca dlq purge --broker-url amqp://guest:guest@localhost/ --force
    """

    async def run_purge() -> None:
        connection = await aio_pika.connect(broker_url)
        try:
            async with connection.channel() as channel:
                # First check how many messages are in the queue
                try:
                    queue_info = await channel.declare_queue(
                        RabbitmqUtils.DEAD_LETTER_QUEUE, passive=True
                    )
                    message_count = queue_info.declaration_result.message_count or 0
                except Exception:
                    click.echo("Dead Letter Queue does not exist or is not accessible.")
                    return

                if message_count == 0:
                    click.echo("Dead Letter Queue is already empty.")
                    return

                if not force:
                    if not click.confirm(
                        f"Are you sure you want to purge {message_count} messages from the DLQ? This cannot be undone."
                    ):
                        click.echo("Purge cancelled.")
                        return

                # Purge the queue
                purged = await RabbitmqUtils.purge_dl_queue(channel)
                click.echo(f"✓ Successfully purged {purged} messages from the DLQ.")

        finally:
            await connection.close()

    asyncio.run(run_purge())


@dlq.command("requeue")
@click.option(
    "--broker-url",
    type=str,
    envvar="BROKER_URL",
    required=True,
    help="The URL for the message broker",
)
@click.option(
    "--queue",
    "queue_filter",
    type=str,
    default=None,
    help="Only requeue messages from a specific original queue (supports partial match)",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Maximum number of messages to requeue",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt",
)
def dlq_requeue(
    broker_url: str,
    queue_filter: str | None,
    limit: int | None,
    force: bool,
) -> None:
    """Requeue messages from the Dead Letter Queue back to their original queues.

    This command retrieves messages from the DLQ and publishes them back to their
    original queues for reprocessing.

    Examples:

    \b
    # Requeue all messages
    jararaca dlq requeue --broker-url amqp://guest:guest@localhost/

    \b
    # Requeue messages from a specific queue
    jararaca dlq requeue --broker-url amqp://guest:guest@localhost/ --queue user.events

    \b
    # Requeue only 5 messages
    jararaca dlq requeue --broker-url amqp://guest:guest@localhost/ --limit 5
    """

    async def run_requeue() -> None:
        parsed_url = urlparse(broker_url)
        query_params = parse_qs(parsed_url.query)

        if "exchange" not in query_params or not query_params["exchange"]:
            click.echo(
                "ERROR: Exchange must be set in the broker URL query string", err=True
            )
            return

        exchange_name = query_params["exchange"][0]

        connection = await aio_pika.connect(broker_url)
        try:
            # Fetch messages (will be consumed for requeuing)
            async with connection.channel() as channel:
                try:
                    queue_info = await channel.declare_queue(
                        RabbitmqUtils.DEAD_LETTER_QUEUE, passive=True
                    )
                    total_count = queue_info.declaration_result.message_count or 0
                except Exception:
                    click.echo("Dead Letter Queue does not exist or is not accessible.")
                    return

                if total_count == 0:
                    click.echo("Dead Letter Queue is empty.")
                    return

                # Get messages without consuming first to show count
                messages_preview = await fetch_dlq_messages(
                    connection, limit=limit, consume=False
                )

                if queue_filter:
                    messages_preview = [
                        (msg, raw)
                        for msg, raw in messages_preview
                        if queue_filter.lower() in msg.original_queue.lower()
                    ]

                if not messages_preview:
                    click.echo(
                        f"No messages found matching queue filter: '{queue_filter}'"
                    )
                    return

                requeue_count = len(messages_preview)

                if not force:
                    if not click.confirm(
                        f"Are you sure you want to requeue {requeue_count} messages?"
                    ):
                        click.echo("Requeue cancelled.")
                        return

            # Now actually consume and requeue messages
            async with connection.channel() as channel:
                queue = await RabbitmqUtils.get_dl_queue(channel)
                exchange = await RabbitmqUtils.get_main_exchange(channel, exchange_name)

                requeued = 0
                errors = 0

                count = 0
                while limit is None or count < limit:
                    try:
                        raw_message = await asyncio.wait_for(
                            queue.get(no_ack=False), timeout=1.0
                        )
                    except asyncio.TimeoutError:
                        break

                    if raw_message is None:
                        break

                    # Apply queue filter
                    headers = raw_message.headers or {}
                    x_death_raw = headers.get("x-death")
                    original_queue = ""
                    if isinstance(x_death_raw, list) and len(x_death_raw) > 0:
                        death_info = x_death_raw[0]
                        if isinstance(death_info, dict):
                            original_queue = str(death_info.get("queue", ""))

                    if (
                        queue_filter
                        and queue_filter.lower() not in original_queue.lower()
                    ):
                        # Requeue back to DLQ (don't process this one)
                        await raw_message.nack(requeue=True)
                        continue

                    try:
                        # Publish to the original routing key
                        routing_key = raw_message.routing_key or original_queue
                        await exchange.publish(
                            aio_pika.Message(
                                body=raw_message.body,
                                content_type=raw_message.content_type,
                                headers={"x-requeued-from-dlq": True},
                            ),
                            routing_key=routing_key,
                        )
                        await raw_message.ack()
                        requeued += 1
                    except Exception as e:
                        click.echo(f"Error requeuing message: {e}", err=True)
                        await raw_message.nack(requeue=True)
                        errors += 1

                    count += 1

                click.echo("\n✓ Requeue complete:")
                click.echo(f"  - Requeued: {requeued}")
                if errors:
                    click.echo(f"  - Errors: {errors}")

        finally:
            await connection.close()

    asyncio.run(run_requeue())
