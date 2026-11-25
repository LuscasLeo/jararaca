# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""`jararaca declare-plan` + `jararaca declare --from-plan` declare exactly what
`jararaca declare APP` did, without importing the application when applying."""

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import pytest
from click.testing import CliRunner, Result

import jararaca.cli as cli
from jararaca.messagebus.declare_plan import (
    SCHEMA_VERSION,
    DeclarePlanError,
    build_declare_plan,
    dump_declare_plan,
    load_declare_plan,
)
from tests.declare_fakes import (
    TEST_APP_MODULE,
    FakeBroker,
    forget_test_app,
    write_test_app,
)

SNAPSHOTS = Path(__file__).parent / "snapshots" / "declare"
ON_ORDER_QUEUE = f"orders.placed.{TEST_APP_MODULE}.OrdersController.on_order"
SECRET = "s3cr3t-Pa55word"
BROKER_URL = f"amqp://app_user:{SECRET}@rabbit.internal:5672/prod?exchange=main_ex"

# Scenarios recorded from `declare APP` before the plan existed
# (tests/snapshots/declare/<name>.json): infra state, failing names, --force.
SCENARIOS: dict[str, tuple[bool, set[str], bool]] = {
    "fresh": (False, set(), False),
    "fresh_force": (False, set(), True),
    "existing_force": (True, set(), True),
    "queue_conflict": (True, {ON_ORDER_QUEUE}, False),
    "queue_conflict_force": (True, {ON_ORDER_QUEUE}, True),
    "exchange_conflict_force": (True, {"main_ex"}, True),
}


@pytest.fixture
def app_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.syspath_prepend(str(tmp_path))
    forget_test_app()
    yield write_test_app(tmp_path)
    forget_test_app()


@pytest.fixture
def plan_file(app_path: str, tmp_path: Path) -> Path:
    path = tmp_path / "declare-plan.json"
    result = CliRunner().invoke(cli.cli, ["declare-plan", app_path, "-o", str(path)])
    assert result.exit_code == 0, result.output
    return path


def run_cli(broker: FakeBroker, *args: str, **env: str) -> Result:
    with patch("aio_pika.connect", broker.connect):
        return CliRunner().invoke(cli.cli, list(args), env=env or None)


def snapshot(name: str) -> Any:
    return json.loads((SNAPSHOTS / f"{name}.json").read_text())


def normalized(broker: FakeBroker) -> Any:
    # Round-trip through JSON so tuples and lists compare like the snapshot.
    return json.loads(json.dumps(broker.normalized()))


class TestEquivalenceWithDeclareApp:
    @pytest.mark.parametrize("scenario", sorted(SCENARIOS))
    def test_plan_applied_declares_what_declare_app_declared(
        self, scenario: str, plan_file: Path
    ) -> None:
        infra_exists, fail_once, force = SCENARIOS[scenario]
        broker = FakeBroker(infra_exists=infra_exists, fail_once=fail_once)

        result = run_cli(
            broker,
            "declare",
            "--from-plan",
            str(plan_file),
            "--broker-url",
            BROKER_URL,
            *(["--force"] if force else []),
        )

        assert result.exit_code == 0, result.output
        assert normalized(broker) == snapshot(scenario)

    @pytest.mark.parametrize("scenario", sorted(SCENARIOS))
    def test_declare_app_still_declares_the_same(
        self, scenario: str, app_path: str
    ) -> None:
        infra_exists, fail_once, force = SCENARIOS[scenario]
        broker = FakeBroker(infra_exists=infra_exists, fail_once=fail_once)

        result = run_cli(
            broker,
            "declare",
            app_path,
            "--broker-url",
            BROKER_URL,
            *(["--force"] if force else []),
        )

        assert result.exit_code == 0, result.output
        assert normalized(broker) == snapshot(scenario)

    def test_a_member_with_both_decorators_gets_both_queues(
        self, app_path: str
    ) -> None:
        plan = build_declare_plan(cli.find_microservice_by_module_path(app_path))
        kinds = {queue.kind for queue in plan.queues if queue.name.endswith(".both")}

        assert kinds == {"message_handler", "scheduled_action"}


class TestPlanFile:
    def test_json_roundtrip(self, app_path: str) -> None:
        plan = build_declare_plan(
            cli.find_microservice_by_module_path(app_path), app_path
        )

        loaded = load_declare_plan(dump_declare_plan(plan))

        assert loaded == plan
        assert loaded.generated_by_version == plan.generated_by_version

    def test_output_is_deterministic(self, app_path: str, tmp_path: Path) -> None:
        first, second = tmp_path / "a.json", tmp_path / "b.json"
        for path in (first, second):
            result = CliRunner().invoke(
                cli.cli, ["declare-plan", app_path, "-o", str(path)]
            )
            assert result.exit_code == 0, result.output

        assert first.read_bytes() == second.read_bytes()

    def test_controller_order_does_not_change_the_file(self, app_path: str) -> None:
        app = cli.find_microservice_by_module_path(app_path)
        reversed_app = replace(app, controllers=list(reversed(app.controllers)))

        assert dump_declare_plan(
            build_declare_plan(app, app_path)
        ) == dump_declare_plan(build_declare_plan(reversed_app, app_path))

    def test_environment_dependent_values_stay_out(self, plan_file: Path) -> None:
        content = plan_file.read_text()
        data = json.loads(content)

        assert data["schema_version"] == SCHEMA_VERSION
        main = next(e for e in data["exchanges"] if e["role"] == "main")
        assert "name" not in main
        assert "main_ex" not in content
        assert "amqp" not in content

    def test_stdout_output(self, app_path: str, plan_file: Path) -> None:
        result = CliRunner().invoke(cli.cli, ["declare-plan", app_path])

        assert result.exit_code == 0, result.output
        assert result.stdout == plan_file.read_text()


class TestApplyWithoutTheApplication:
    def test_apply_does_not_import_the_application(
        self, plan_file: Path, app_path: str
    ) -> None:
        forget_test_app()
        broker = FakeBroker()

        # APP_PATH set in the environment, as it may be in the image, is ignored.
        result = run_cli(
            broker,
            "declare",
            "--from-plan",
            str(plan_file),
            "--broker-url",
            BROKER_URL,
            APP_PATH=app_path,
        )

        assert result.exit_code == 0, result.output
        assert TEST_APP_MODULE not in sys.modules
        assert "ignoring the application path" in result.stderr

    def test_main_exchange_comes_from_the_broker_url(self, plan_file: Path) -> None:
        declared: dict[str, set[str]] = {}
        for exchange in ("alpha_ex", "beta_ex"):
            broker = FakeBroker()
            url = f"amqp://guest:guest@localhost/vhost?exchange={exchange}"
            result = run_cli(
                broker, "declare", "--from-plan", str(plan_file), "--broker-url", url
            )
            assert result.exit_code == 0, result.output
            assert broker.connected_urls == [url]
            declared[exchange] = {
                op[2]
                for ops in broker.channels
                for op in ops
                if op[0] == "bind" and op[1] != "dlq"
            }

        assert declared == {"alpha_ex": {"alpha_ex"}, "beta_ex": {"beta_ex"}}

    def test_missing_app_and_plan_is_a_usage_error(self) -> None:
        result = run_cli(FakeBroker(), "declare", "--broker-url", BROKER_URL)

        assert result.exit_code == 2
        assert "--from-plan" in result.output


class TestInvalidPlans:
    def write(self, tmp_path: Path, content: str) -> Path:
        path = tmp_path / "plan.json"
        path.write_text(content)
        return path

    def test_unknown_schema_version_fails_before_connecting(
        self, plan_file: Path, tmp_path: Path
    ) -> None:
        data = json.loads(plan_file.read_text())
        data["schema_version"] = SCHEMA_VERSION + 1
        broker = FakeBroker()

        result = run_cli(
            broker,
            "declare",
            "--from-plan",
            str(self.write(tmp_path, json.dumps(data))),
            "--broker-url",
            BROKER_URL,
        )

        assert result.exit_code != 0
        assert f"Unsupported declare plan schema_version {SCHEMA_VERSION + 1}" in (
            result.stderr
        )
        assert broker.connected_urls == []

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            ("not json", "not valid JSON"),
            ("[]", "expected a JSON object"),
            ('{"schema_version": true}', "Unsupported declare plan schema_version"),
            ('{"schema_version": 1}', "missing 'exchanges'"),
        ],
    )
    def test_malformed_plans_are_rejected(self, content: str, message: str) -> None:
        with pytest.raises(DeclarePlanError, match=message):
            load_declare_plan(content)

    def test_main_exchange_name_in_the_plan_is_rejected(self, plan_file: Path) -> None:
        data = json.loads(plan_file.read_text())
        for exchange in data["exchanges"]:
            if exchange["role"] == "main":
                exchange["name"] = "frozen_ex"

        with pytest.raises(DeclarePlanError, match="comes from the broker URL"):
            load_declare_plan(json.dumps(data))

    def test_plan_from_another_jararaca_version_warns(
        self, plan_file: Path, tmp_path: Path
    ) -> None:
        data = json.loads(plan_file.read_text())
        data["generated_by"]["version"] = "0.0.1"

        result = run_cli(
            FakeBroker(),
            "declare",
            "--from-plan",
            str(self.write(tmp_path, json.dumps(data))),
            "--broker-url",
            BROKER_URL,
        )

        assert result.exit_code == 0, result.output
        assert "generated by jararaca 0.0.1" in result.stderr


class TestCredentialsNeverPrinted:
    def test_declare_app(self, app_path: str) -> None:
        result = run_cli(FakeBroker(), "declare", app_path, "--broker-url", BROKER_URL)

        assert result.exit_code == 0, result.output
        assert SECRET not in result.output
        assert "app_user:***@rabbit.internal:5672/prod?exchange=main_ex" in (
            result.output
        )

    def test_declare_from_plan(self, plan_file: Path) -> None:
        result = run_cli(
            FakeBroker(),
            "declare",
            "--from-plan",
            str(plan_file),
            "--broker-url",
            BROKER_URL,
        )

        assert result.exit_code == 0, result.output
        assert SECRET not in result.output

    def test_connection_failure(self, plan_file: Path) -> None:
        """A real connection attempt to a closed port, so the error text is the
        library's own."""
        url = f"amqp://app_user:{SECRET}@127.0.0.1:1/prod?exchange=main_ex"

        result = CliRunner().invoke(
            cli.cli, ["declare", "--from-plan", str(plan_file), "--broker-url", url]
        )

        assert result.exit_code != 0
        assert SECRET not in result.output
        assert result.exception is None or SECRET not in str(result.exception)

    def test_invalid_plan(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.json"
        path.write_text('{"schema_version": 99}')

        result = run_cli(
            FakeBroker(),
            "declare",
            "--from-plan",
            str(path),
            "--broker-url",
            BROKER_URL,
        )

        assert result.exit_code != 0
        assert SECRET not in result.output


def test_declare_worker_infrastructure_api_is_kept(app_path: str) -> None:
    """The programmatic entry point keeps its signature and behaviour."""
    broker = FakeBroker()
    app = cli.find_microservice_by_module_path(app_path)

    with patch("aio_pika.connect", broker.connect):
        asyncio.run(cli.declare_worker_infrastructure(BROKER_URL, app, False, False))

    assert normalized(broker) == snapshot("fresh")


class TestForceRecreatesExistingInfrastructure:
    """--force (or a confirmed --interactive prompt) deletes the main exchange and
    the dead letter exchange and queue when they exist, before declaring them."""

    INFRA_DELETE = [
        ["exchange_delete", "main_ex"],
        ["exchange_delete", "dlx"],
        ["queue_delete", "dlq"],
    ]

    def infra_deleted(self, broker: FakeBroker) -> bool:
        return self.INFRA_DELETE in broker.channels

    def apply(
        self, broker: FakeBroker, plan_file: Path, *flags: str, input: str = ""
    ) -> Result:
        with patch("aio_pika.connect", broker.connect):
            return CliRunner().invoke(
                cli.cli,
                [
                    "declare",
                    "--from-plan",
                    str(plan_file),
                    "--broker-url",
                    BROKER_URL,
                    *flags,
                ],
                input=input,
            )

    def test_force_deletes_existing_infrastructure(self, plan_file: Path) -> None:
        broker = FakeBroker(infra_exists=True)

        result = self.apply(broker, plan_file, "--force")

        assert result.exit_code == 0, result.output
        assert self.infra_deleted(broker)

    def test_force_on_a_fresh_broker_deletes_nothing(self, plan_file: Path) -> None:
        broker = FakeBroker(infra_exists=False)

        result = self.apply(broker, plan_file, "--force")

        assert result.exit_code == 0, result.output
        assert not self.infra_deleted(broker)

    def test_without_force_existing_infrastructure_is_kept(
        self, plan_file: Path
    ) -> None:
        broker = FakeBroker(infra_exists=True)

        result = self.apply(broker, plan_file)

        assert result.exit_code == 0, result.output
        assert not self.infra_deleted(broker)

    @pytest.mark.parametrize(("answer", "deleted"), [("y\n", True), ("n\n", False)])
    def test_interactive_asks_before_deleting_existing_infrastructure(
        self, plan_file: Path, answer: str, deleted: bool
    ) -> None:
        broker = FakeBroker(infra_exists=True)

        result = self.apply(broker, plan_file, "--interactive-mode", input=answer)

        assert result.exit_code == 0, result.output
        assert "Existing infrastructure found for exchange 'main_ex'" in result.output
        assert self.infra_deleted(broker) is deleted
