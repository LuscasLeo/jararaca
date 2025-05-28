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
from urllib.parse import urlparse, urlunsplit

import aio_pika
import click
import uvicorn
from mako.template import Template

from jararaca.messagebus import worker as worker_v1
from jararaca.messagebus import worker_v2 as worker_v2_mod
from jararaca.microservice import Microservice
from jararaca.presentation.http_microservice import HttpMicroservice
from jararaca.presentation.server import create_http_server
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


async def declare_worker_infrastructure(
    url: str,
    exchange: str,
    app: Microservice,
    passive_declare: bool = False,
) -> None:
    """
    Declare the infrastructure (exchanges and queues) for worker v1.
    """
    connection = await aio_pika.connect(url)
    channel = await connection.channel()

    await channel.set_qos(prefetch_count=1)

    # Declare main exchange
    main_ex = await RabbitmqUtils.declare_main_exchange(
        channel=channel,
        exchange_name=exchange,
        passive=passive_declare,
    )

    # Declare dead letter infrastructure
    dlx, dlq = await RabbitmqUtils.declare_dl_kit(
        channel=channel, passive=passive_declare
    )

    # Find all message handlers to declare their queues
    from jararaca.di import Container
    from jararaca.messagebus.decorators import MessageBusController

    container = Container(app)

    for instance_type in app.controllers:
        controller = MessageBusController.get_messagebus(instance_type)
        if controller is None:
            continue

        instance: Any = container.get_by_type(instance_type)
        factory = controller.get_messagebus_factory()
        handlers, _ = factory(instance)

        for handler in handlers:
            queue_name = f"{handler.message_type.MESSAGE_TOPIC}.{handler.callable.__module__}.{handler.callable.__qualname__}"
            routing_key = f"{handler.message_type.MESSAGE_TOPIC}.#"

            queue = await channel.declare_queue(
                passive=passive_declare,
                name=queue_name,
                arguments={
                    "x-dead-letter-exchange": dlx.name,
                    "x-dead-letter-routing-key": dlq.name,
                },
                durable=True,
            )

            await queue.bind(exchange=main_ex, routing_key=routing_key)
            click.echo(f"✓ Declared queue: {queue_name} (routing key: {routing_key})")

    await channel.close()
    await connection.close()


async def declare_worker_v2_infrastructure(
    broker_url: str,
    app: Microservice,
    passive_declare: bool = False,
) -> None:
    """
    Declare the infrastructure (exchanges and queues) for worker v2.
    """
    from urllib.parse import parse_qs, urlparse

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

    await channel.set_qos(prefetch_count=1)

    # Declare main exchange
    await RabbitmqUtils.declare_main_exchange(
        channel=channel,
        exchange_name=exchange,
        passive=passive_declare,
    )

    # Declare dead letter infrastructure
    dlx = await RabbitmqUtils.declare_dl_exchange(
        channel=channel, passive=passive_declare
    )
    dlq = await RabbitmqUtils.declare_dl_queue(channel=channel, passive=passive_declare)
    await dlq.bind(dlx, routing_key=RabbitmqUtils.DEAD_LETTER_EXCHANGE)

    # Find all message handlers and scheduled actions
    from jararaca.di import Container
    from jararaca.messagebus.decorators import MessageBusController

    container = Container(app)

    for instance_type in app.controllers:
        controller = MessageBusController.get_messagebus(instance_type)
        if controller is None:
            continue

        instance: Any = container.get_by_type(instance_type)
        factory = controller.get_messagebus_factory()
        handlers, scheduled_actions = factory(instance)

        # Declare queues for message handlers
        for handler in handlers:
            queue_name = f"{handler.message_type.MESSAGE_TOPIC}.{handler.callable.__module__}.{handler.callable.__qualname__}"
            routing_key = f"{handler.message_type.MESSAGE_TOPIC}.#"

            queue = await RabbitmqUtils.declare_queue(
                channel=channel, queue_name=queue_name, passive=passive_declare
            )
            await queue.bind(exchange=exchange, routing_key=routing_key)
            click.echo(
                f"✓ Declared message handler queue: {queue_name} (routing key: {routing_key})"
            )

        # Declare queues for scheduled actions
        for scheduled_action in scheduled_actions:
            queue_name = f"{scheduled_action.callable.__module__}.{scheduled_action.callable.__qualname__}"
            routing_key = queue_name

            queue = await RabbitmqUtils.declare_queue(
                channel=channel, queue_name=queue_name, passive=passive_declare
            )
            await queue.bind(exchange=exchange, routing_key=routing_key)
            click.echo(
                f"✓ Declared scheduled action queue: {queue_name} (routing key: {routing_key})"
            )

    await channel.close()
    await connection.close()


async def declare_scheduler_v2_infrastructure(
    broker_url: str,
    app: Microservice,
    passive_declare: bool = False,
) -> None:
    """
    Declare the infrastructure (exchanges and queues) for scheduler v2.
    """
    from urllib.parse import parse_qs, urlparse

    from jararaca.scheduler.decorators import ScheduledAction

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

    await channel.set_qos(prefetch_count=1)

    # Declare exchange for scheduler
    await channel.declare_exchange(
        name=exchange,
        type="topic",
        durable=True,
        auto_delete=False,
        passive=passive_declare,
    )

    # Find all scheduled actions and declare their queues
    from jararaca.di import Container
    from jararaca.messagebus.decorators import MessageBusController

    container = Container(app)
    scheduled_actions: list[Any] = []

    for instance_type in app.controllers:
        controller = MessageBusController.get_messagebus(instance_type)
        if controller is None:
            continue

        instance: Any = container.get_by_type(instance_type)
        factory = controller.get_messagebus_factory()
        _, actions = factory(instance)
        scheduled_actions.extend(actions)

    for scheduled_action in scheduled_actions:
        queue_name = ScheduledAction.get_function_id(scheduled_action.callable)
        queue = await channel.declare_queue(
            name=queue_name,
            durable=True,
            passive=passive_declare,
        )
        await queue.bind(
            exchange=exchange,
            routing_key=queue_name,
        )
        click.echo(f"✓ Declared scheduler queue: {queue_name}")

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
)
def worker(
    app_path: str,
    url: str,
    username: str | None,
    password: str | None,
    exchange: str,
    prefetch_count: int,
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

    config = worker_v1.AioPikaWorkerConfig(
        url=url,
        exchange=exchange,
        prefetch_count=prefetch_count,
    )

    worker_v1.MessageBusWorker(app, config=config).start_sync(
        passive_declare=passive_declare,
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
def worker_v2(app_path: str, broker_url: str, backend_url: str) -> None:

    app = find_microservice_by_module_path(app_path)

    worker_v2_mod.MessageBusWorker(
        app=app,
        broker_url=broker_url,
        backend_url=backend_url,
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
def scheduler(
    app_path: str,
    interval: int,
) -> None:
    app = find_microservice_by_module_path(app_path)

    Scheduler(app, interval=interval).run()


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
def scheduler_v2(
    interval: int,
    broker_url: str,
    backend_url: str,
    app_path: str,
) -> None:

    app = find_microservice_by_module_path(app_path)
    scheduler = SchedulerV2(
        app=app,
        interval=interval,
        backend_url=backend_url,
        broker_url=broker_url,
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


@cli.command("declare-queues-v1")
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
def declare_queues_v1(
    app_path: str,
    broker_url: str | None,
    exchange: str,
    passive_declare: bool,
) -> None:
    """
    Declare RabbitMQ infrastructure (exchanges and queues) for worker v1.

    This command pre-declares the necessary exchanges and queues that worker v1
    needs, without starting the actual consumption processes.

    Environment variables:
    - BROKER_URL: Broker URL (e.g., amqp://guest:guest@localhost/)
    - EXCHANGE: Exchange name (defaults to 'jararaca_ex')

    Examples:

    \b
    # Declare worker v1 infrastructure
    jararaca declare-queues-v1 myapp:app --broker-url amqp://guest:guest@localhost/

    \b
    # Use environment variables
    export BROKER_URL="amqp://guest:guest@localhost/"
    export EXCHANGE="my_exchange"
    jararaca declare-queues-v1 myapp:app
    """

    app = find_microservice_by_module_path(app_path)

    async def run_declarations() -> None:
        if not broker_url:
            click.echo(
                "ERROR: --broker-url is required or set BROKER_URL environment variable",
                err=True,
            )
            return

        click.echo(
            f"→ Declaring worker v1 infrastructure (URL: {broker_url}, Exchange: {exchange})"
        )

        try:
            await declare_worker_infrastructure(
                broker_url, exchange, app, passive_declare
            )
            click.echo("✓ Worker v1 infrastructure declared successfully!")
        except Exception as e:
            click.echo(
                f"ERROR: Failed to declare worker v1 infrastructure: {e}", err=True
            )
            raise

    asyncio.run(run_declarations())


@cli.command("declare-queues-v2")
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
def declare_queues_v2(
    app_path: str,
    broker_url: str | None,
    exchange: str,
    passive_declare: bool,
) -> None:
    """
    Declare RabbitMQ infrastructure (exchanges and queues) for worker v2 and scheduler v2.

    This command pre-declares the necessary exchanges and queues that worker v2
    and scheduler v2 need, without starting the actual consumption processes.

    Environment variables:
    - BROKER_URL: Broker URL (e.g., amqp://guest:guest@localhost/)
    - EXCHANGE: Exchange name (defaults to 'jararaca_ex')

    Examples:

    \b
    # Declare worker v2 and scheduler v2 infrastructure
    jararaca declare-queues-v2 myapp:app --broker-url amqp://guest:guest@localhost/

    \b
    # Use environment variables
    export BROKER_URL="amqp://guest:guest@localhost/"
    export EXCHANGE="my_exchange"
    jararaca declare-queues-v2 myapp:app
    """

    app = find_microservice_by_module_path(app_path)

    async def run_declarations() -> None:
        if not broker_url:
            click.echo(
                "ERROR: --broker-url is required or set BROKER_URL environment variable",
                err=True,
            )
            return

        # For v2, create the broker URL with exchange parameter
        v2_broker_url = f"{broker_url}?exchange={exchange}"

        click.echo(f"→ Declaring worker v2 infrastructure (URL: {v2_broker_url})")
        click.echo(f"→ Declaring scheduler v2 infrastructure (URL: {v2_broker_url})")

        try:
            await asyncio.gather(
                declare_worker_v2_infrastructure(v2_broker_url, app, passive_declare),
                declare_scheduler_v2_infrastructure(
                    v2_broker_url, app, passive_declare
                ),
            )
            click.echo(
                "✓ Worker v2 and scheduler v2 infrastructure declared successfully!"
            )
        except Exception as e:
            click.echo(f"ERROR: Failed to declare v2 infrastructure: {e}", err=True)
            raise

    asyncio.run(run_declarations())
