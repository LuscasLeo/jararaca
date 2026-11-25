# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
What `jararaca declare` declares on the broker, as plain data.

A plan is built from the application (`build_declare_plan`), which needs the app
imported, and written as JSON (`dump_declare_plan`). Applying it later
(`jararaca declare --from-plan`) reads the JSON alone, so the step that talks to
the broker does not import the application.

Only what comes from code is frozen in the plan: queue names, routing keys and
declaration properties. Everything that comes from the environment stays out of
it and is resolved when the plan is applied, from the broker URL: the main
exchange name (`?exchange=`), host, vhost and credentials.
"""

import importlib.metadata
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from jararaca.messagebus.aiopika import gen_queue_name, gen_routing_key
from jararaca.messagebus.decorators import MessageBusController, MessageHandler
from jararaca.microservice import Microservice
from jararaca.reflect.controller_inspect import inspect_controller
from jararaca.scheduler.decorators import ScheduledAction
from jararaca.utils.rabbitmq_utils import RabbitmqUtils

SCHEMA_VERSION = 1

QueueKind = Literal["message_handler", "scheduled_action"]
QUEUE_KINDS: tuple[QueueKind, ...] = ("message_handler", "scheduled_action")


class DeclarePlanError(ValueError):
    """A plan file that cannot be applied: unknown schema or malformed content."""


@dataclass(frozen=True)
class ExchangeSpec:
    type: str
    durable: bool
    auto_delete: bool


@dataclass(frozen=True)
class DeadLetterSpec:
    exchange_name: str
    exchange: ExchangeSpec
    queue_name: str
    queue_durable: bool
    queue_arguments: dict[str, Any]
    binding_routing_key: str


@dataclass(frozen=True)
class QueueSpec:
    kind: QueueKind
    name: str
    durable: bool
    arguments: dict[str, Any]
    # Bound to the main exchange, whose name comes from the broker URL.
    routing_key: str


@dataclass(frozen=True)
class DeclarePlan:
    main_exchange: ExchangeSpec
    dead_letter: DeadLetterSpec
    queues: tuple[QueueSpec, ...]
    app: str | None = None
    generated_by_version: str = field(default="unknown", compare=False)


def jararaca_version() -> str:
    try:
        return importlib.metadata.version("jararaca")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _infrastructure() -> tuple[ExchangeSpec, DeadLetterSpec]:
    main_exchange = ExchangeSpec(
        type=RabbitmqUtils.MAIN_EXCHANGE_TYPE, durable=True, auto_delete=False
    )
    dead_letter = DeadLetterSpec(
        exchange_name=RabbitmqUtils.DEAD_LETTER_EXCHANGE,
        exchange=ExchangeSpec(
            type=RabbitmqUtils.DEAD_LETTER_EXCHANGE_TYPE,
            durable=True,
            auto_delete=False,
        ),
        queue_name=RabbitmqUtils.DEAD_LETTER_QUEUE,
        queue_durable=True,
        queue_arguments=RabbitmqUtils.dead_letter_queue_arguments(),
        binding_routing_key=RabbitmqUtils.DEAD_LETTER_EXCHANGE,
    )
    return main_exchange, dead_letter


def build_declare_plan(app: Microservice, app_path: str | None = None) -> DeclarePlan:
    """
    Everything `declare` declares for an application, read from its controller
    classes (no controller is instantiated). Every handler and scheduled action is
    included: `declare` does not apply the worker's --groups/--handlers filters. A
    member carrying both decorators gets both queues.
    """
    queues: dict[str, QueueSpec] = {}

    for controller_type in app.controllers:
        if MessageBusController.get_last(controller_type) is None:
            continue

        _, members = inspect_controller(controller_type)
        for member in members.values():
            function = member.member_function

            message_handler = MessageHandler.get_last(function)
            if message_handler is not None:
                name = gen_queue_name(message_handler.message_type, function)
                queues.setdefault(
                    name,
                    QueueSpec(
                        kind="message_handler",
                        name=name,
                        durable=True,
                        arguments=RabbitmqUtils.worker_queue_arguments(),
                        routing_key=gen_routing_key(message_handler.message_type),
                    ),
                )

            if ScheduledAction.get_last(function) is not None:
                name = f"{function.__module__}.{function.__qualname__}"
                queues.setdefault(
                    name,
                    QueueSpec(
                        kind="scheduled_action",
                        name=name,
                        durable=True,
                        arguments=RabbitmqUtils.scheduled_action_queue_arguments(),
                        routing_key=name,
                    ),
                )

    main_exchange, dead_letter = _infrastructure()
    return DeclarePlan(
        main_exchange=main_exchange,
        dead_letter=dead_letter,
        queues=tuple(queues[name] for name in sorted(queues)),
        app=app_path,
        generated_by_version=jararaca_version(),
    )


def _exchange_to_dict(spec: ExchangeSpec) -> dict[str, Any]:
    return {"type": spec.type, "durable": spec.durable, "auto_delete": spec.auto_delete}


def declare_plan_to_dict(plan: DeclarePlan) -> dict[str, Any]:
    dead_letter = plan.dead_letter
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": {"tool": "jararaca", "version": plan.generated_by_version},
        "app": plan.app,
        "exchanges": [
            {"role": "main", **_exchange_to_dict(plan.main_exchange)},
            {
                "role": "dead_letter",
                "name": dead_letter.exchange_name,
                **_exchange_to_dict(dead_letter.exchange),
            },
        ],
        "dead_letter_queue": {
            "name": dead_letter.queue_name,
            "durable": dead_letter.queue_durable,
            "arguments": dead_letter.queue_arguments,
            "binding": {
                "exchange": dead_letter.exchange_name,
                "routing_key": dead_letter.binding_routing_key,
            },
        },
        "queues": [
            {
                "kind": queue.kind,
                "name": queue.name,
                "durable": queue.durable,
                "arguments": queue.arguments,
                "routing_key": queue.routing_key,
            }
            for queue in sorted(plan.queues, key=lambda queue: queue.name)
        ],
    }


def dump_declare_plan(plan: DeclarePlan) -> str:
    """Deterministic JSON: sorted keys and queues, no timestamps."""
    return json.dumps(declare_plan_to_dict(plan), indent=2, sort_keys=True) + "\n"


def _require(data: Any, key: str, kind: type, where: str) -> Any:
    if not isinstance(data, dict) or key not in data:
        raise DeclarePlanError(f"Invalid declare plan: missing '{key}' in {where}")
    value = data[key]
    # bool is a subclass of int, so only accept it where a bool is expected.
    if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
        raise DeclarePlanError(
            f"Invalid declare plan: '{key}' in {where} has an unexpected type"
        )
    return value


def _exchange_from_dict(data: Any, where: str) -> ExchangeSpec:
    return ExchangeSpec(
        type=_require(data, "type", str, where),
        durable=_require(data, "durable", bool, where),
        auto_delete=_require(data, "auto_delete", bool, where),
    )


def load_declare_plan(text: str) -> DeclarePlan:
    """Parse a plan written by `dump_declare_plan`, rejecting unknown schemas."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise DeclarePlanError(f"Invalid declare plan: not valid JSON ({e})") from None

    if not isinstance(data, dict):
        raise DeclarePlanError("Invalid declare plan: expected a JSON object")

    schema_version = data.get("schema_version")
    if schema_version != SCHEMA_VERSION or isinstance(schema_version, bool):
        raise DeclarePlanError(
            f"Unsupported declare plan schema_version {schema_version!r}: this "
            f"jararaca ({jararaca_version()}) applies schema_version "
            f"{SCHEMA_VERSION}. Regenerate the plan with `jararaca declare-plan` "
            "using the same jararaca version that applies it."
        )

    exchanges = _require(data, "exchanges", list, "the plan")
    by_role: dict[str, Any] = {}
    for exchange in exchanges:
        role = _require(exchange, "role", str, "an exchange")
        if role in by_role:
            raise DeclarePlanError(f"Invalid declare plan: duplicate exchange '{role}'")
        by_role[role] = exchange
    if set(by_role) != {"main", "dead_letter"}:
        raise DeclarePlanError(
            "Invalid declare plan: expected exactly the 'main' and 'dead_letter' "
            f"exchanges, got {sorted(by_role)}"
        )
    if "name" in by_role["main"]:
        raise DeclarePlanError(
            "Invalid declare plan: the main exchange name comes from the broker URL "
            "(?exchange=) and must not be in the plan"
        )

    dlq = _require(data, "dead_letter_queue", dict, "the plan")
    dlq_binding = _require(dlq, "binding", dict, "dead_letter_queue")
    dead_letter_exchange_name = _require(
        by_role["dead_letter"], "name", str, "the dead_letter exchange"
    )
    if _require(dlq_binding, "exchange", str, "dead_letter_queue.binding") != (
        dead_letter_exchange_name
    ):
        raise DeclarePlanError(
            "Invalid declare plan: the dead letter queue must be bound to the dead "
            "letter exchange"
        )

    queues: list[QueueSpec] = []
    for queue in _require(data, "queues", list, "the plan"):
        kind = _require(queue, "kind", str, "a queue")
        if kind not in QUEUE_KINDS:
            raise DeclarePlanError(f"Invalid declare plan: unknown queue kind '{kind}'")
        queues.append(
            QueueSpec(
                kind=kind,
                name=_require(queue, "name", str, "a queue"),
                durable=_require(queue, "durable", bool, "a queue"),
                arguments=_require(queue, "arguments", dict, "a queue"),
                routing_key=_require(queue, "routing_key", str, "a queue"),
            )
        )

    generated_by = data.get("generated_by")
    version = (
        generated_by.get("version", "unknown")
        if isinstance(generated_by, dict)
        else "unknown"
    )
    app = data.get("app")

    return DeclarePlan(
        main_exchange=_exchange_from_dict(by_role["main"], "the main exchange"),
        dead_letter=DeadLetterSpec(
            exchange_name=dead_letter_exchange_name,
            exchange=_exchange_from_dict(
                by_role["dead_letter"], "the dead_letter exchange"
            ),
            queue_name=_require(dlq, "name", str, "dead_letter_queue"),
            queue_durable=_require(dlq, "durable", bool, "dead_letter_queue"),
            queue_arguments=_require(dlq, "arguments", dict, "dead_letter_queue"),
            binding_routing_key=_require(
                dlq_binding, "routing_key", str, "dead_letter_queue.binding"
            ),
        ),
        queues=tuple(sorted(queues, key=lambda queue: queue.name)),
        app=app if isinstance(app, str) else None,
        generated_by_version=str(version),
    )
