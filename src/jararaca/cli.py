import asyncio
import importlib
import importlib.resources
import multiprocessing
import os
import sys
import time
import traceback
from codecs import StreamWriter
from pathlib import Path
from typing import Any, Callable
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
        controller_spec = MessageBusController.get_messagebus(instance_type)
        if controller_spec is None:
            continue

        _, members = inspect_controller(instance_type)

        # Process each member (method) in the controller
        for _, member in members.items():
            # Check if it's a message handler
            await declare_message_handler_queue(
                connection, member, exchange, force, interactive_mode
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
) -> None:
    """
    Declare a queue for a message handler if the member is one.
    """
    message_handler = MessageHandler.get_message_incoming(member.member_function)
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
    scheduled_action = ScheduledAction.get_scheduled_action(member.member_function)
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
def worker(
    app_path: str,
    broker_url: str,
    backend_url: str,
    handlers: str | None,
    reload: bool,
    src_dir: str,
) -> None:
    """Start a message bus worker that processes asynchronous messages from a message queue."""

    if reload:
        process_args = {
            "app_path": app_path,
            "broker_url": broker_url,
            "backend_url": backend_url,
            "handlers": handlers,
        }
        run_with_reload_watcher(process_args, run_worker_process, src_dir)
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
def beat(
    interval: int,
    broker_url: str,
    backend_url: str,
    app_path: str,
    actions: str | None = None,
    reload: bool = False,
    src_dir: str = "src",
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
        run_with_reload_watcher(process_args, run_beat_process, src_dir)
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
                self.active_process.join(timeout=5)

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

    # Set up observer
    observer = Observer()
    observer.schedule(PyFileChangeHandler(), src_dir, recursive=True)  # type: ignore[no-untyped-call]
    observer.start()  # type: ignore[no-untyped-call]

    click.echo(f"Watching for changes in {os.path.abspath(src_dir)}...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()  # type: ignore[no-untyped-call]
        if process.is_alive():
            click.echo("Stopping process...")
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join()
        click.echo("Reload mode stopped")
    observer.join()
