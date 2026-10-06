# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the ``const`` enum style (``gen-tsi --enum-style const``).
"""

from enum import Enum
from typing import Literal

from click.testing import CliRunner
from pydantic import BaseModel

from jararaca import Get, Microservice, RestController
from jararaca.cli import cli
from jararaca.tools.typescript.interface_parser import (
    parse_type_to_typescript_interface,
    with_parse_context,
    write_microservice_to_typescript_interface,
)


class Status(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Ticket(BaseModel):
    status: Status
    fixed: Literal[Status.ACTIVE]


@RestController("/tickets")
class TicketsController:
    @Get("/")
    async def read(self) -> Ticket:
        raise NotImplementedError


APP = Microservice(controllers=[TicketsController])


def test_enum_style_defaults_to_typescript_enum() -> None:
    _, interface = parse_type_to_typescript_interface(Status)

    assert "export enum Status" in interface
    assert "as const" not in interface


def test_const_enum_is_an_object_with_a_type_alias() -> None:
    with with_parse_context("const"):
        _, interface = parse_type_to_typescript_interface(Status)

    assert interface == (
        "export const Status = {\n"
        '\tACTIVE: "active",\n'
        '\tINACTIVE: "inactive",\n'
        "} as const;\n"
        "export type Status = (typeof Status)[keyof typeof Status];\n"
    )


def test_enum_member_literal_is_a_value_in_const_style() -> None:
    default_output = write_microservice_to_typescript_interface(APP)
    const_output = write_microservice_to_typescript_interface(APP, "const")

    # A TS enum member is also a type; a member of an `as const` object is not
    assert "fixed: Status.ACTIVE;" in default_output
    assert "fixed: typeof Status.ACTIVE;" in const_output
    assert "status: Status;" in const_output


def test_enum_style_does_not_leak_between_generations() -> None:
    write_microservice_to_typescript_interface(APP, "const")

    assert "export enum Status" in write_microservice_to_typescript_interface(APP)


def test_gen_tsi_enum_style_option() -> None:
    runner = CliRunner()

    default = runner.invoke(
        cli, ["gen-tsi", "tests.test_typescript_enum_style:APP", "--stdout"]
    )
    const = runner.invoke(
        cli,
        [
            "gen-tsi",
            "tests.test_typescript_enum_style:APP",
            "--stdout",
            "--enum-style",
            "const",
        ],
    )

    assert default.exit_code == 0, default.output
    assert "export enum Status" in default.output
    assert const.exit_code == 0, const.output
    assert "export const Status = {" in const.output
    assert "export enum" not in const.output


def test_gen_tsi_rejects_unknown_enum_style() -> None:
    result = CliRunner().invoke(
        cli,
        [
            "gen-tsi",
            "tests.test_typescript_enum_style:APP",
            "--stdout",
            "--enum-style",
            "nope",
        ],
    )

    assert result.exit_code != 0
    assert "Invalid value for '--enum-style'" in result.output
