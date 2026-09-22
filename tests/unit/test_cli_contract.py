"""Every CLI command must be invocable and must say what it does.

The operator-facing commands are the only way to run a cycle, settle labels, or
take a capacity baseline, and most of them are covered nowhere else: a typo in
an ``Annotated`` parameter or a signature Typer cannot build surfaces when the
command is invoked, not when the module is imported. Asking each command for
its help builds it, which turns that class of breakage into a failing test.
"""

from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from edgar_moe.cli import app

runner = CliRunner()

MINIMUM_HELP_CHARACTERS = 30


def command_names(typer_app: typer.Typer) -> list[str]:
    names: list[str] = []
    for command in typer_app.registered_commands:
        name = command.name or (
            command.callback.__name__.replace("_", "-") if command.callback else ""
        )
        if name:
            names.append(name)
    for group in typer_app.registered_groups:
        prefix = group.name or ""
        if group.typer_instance is None:
            continue
        names.extend(f"{prefix} {child}".strip() for child in command_names(group.typer_instance))
    return sorted(names)


COMMANDS = command_names(app)


def test_the_command_surface_is_not_silently_lost() -> None:
    # A registration mistake that drops commands would otherwise make every
    # parametrized case below vanish rather than fail.
    assert len(COMMANDS) >= 30
    assert "forward-settle" in COMMANDS
    assert "capacity-baseline" in COMMANDS


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_builds_and_documents_itself(command: str) -> None:
    result = runner.invoke(app, [*command.split(" "), "--help"])

    assert result.exit_code == 0, result.output
    body = result.output.split("Options", 1)[0]
    assert len(body.strip()) >= MINIMUM_HELP_CHARACTERS, f"{command} has no usable help text"


def test_the_application_lists_every_command_in_its_own_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    listed = result.output
    missing = [command for command in COMMANDS if " " not in command and command not in listed]
    assert not missing, f"not listed in the top-level help: {', '.join(missing)}"


def test_an_unknown_command_fails_rather_than_doing_something_else() -> None:
    result = runner.invoke(app, ["forward-sette"])  # a plausible typo

    assert result.exit_code != 0
    assert "No such command" in result.output or "Usage" in result.output


@pytest.mark.parametrize(
    "command",
    ["forward-settle", "forward-forecast", "build-dataset"],
)
def test_a_pipeline_command_refuses_to_run_without_its_inputs(command: str) -> None:
    # These commands write evidence or spend compute, so a missing required
    # option has to stop them at the boundary instead of part way through.
    result = runner.invoke(app, [command])

    assert result.exit_code != 0
    assert "Missing option" in result.output or "Usage" in result.output
