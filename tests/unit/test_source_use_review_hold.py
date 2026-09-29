from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from typer.testing import CliRunner

import edgar_moe.cli as cli
from edgar_moe.data.source_rights import SOURCE_USE_HELD_COMMANDS

WEEKLY = Path(".github/workflows/weekly-signals.yml")
PREWARM = Path(".github/workflows/forward-cache-prewarm.yml")


def _workflow(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return cast(dict[str, Any], document)


@pytest.mark.parametrize(
    "arguments",
    [
        ["refresh-data"],
        ["build-dataset", "--checkpoint", "does-not-exist"],
        ["run-study", "--dataset-dir", "does-not-exist"],
        ["forward-forecast", "--dataset-dir", "does-not-exist"],
    ],
)
def test_held_cli_commands_fail_before_loading_runtime_or_inputs(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    monkeypatch.setenv("EDGAR_MOE_SOURCE_USE_REVIEW", "cleared")
    monkeypatch.setattr(cli, "runtime_settings", lambda: pytest.fail("runtime loaded before hold"))

    result = CliRunner().invoke(cli.app, arguments)

    assert result.exit_code != 0
    message = " ".join(result.output.split())
    assert "source-use review" in message and "#280" in message
    assert "no runtime bypass" in message


def test_only_the_documented_provider_and_research_cli_surface_is_held() -> None:
    commands = {
        command.name
        for command in cli.app.registered_commands
        if command.callback is not None
        and getattr(command.callback, "__source_use_review_held__", False)
    }
    assert commands == SOURCE_USE_HELD_COMMANDS

    for allowed in (
        "demo",
        "validate-config",
        "ingest-sec",
        "verify-universe-screen",
        "forward-status",
    ):
        assert allowed not in SOURCE_USE_HELD_COMMANDS


def test_help_and_synthetic_demo_remain_available() -> None:
    runner = CliRunner()

    held_help = runner.invoke(cli.app, ["refresh-data", "--help"])
    demo_help = runner.invoke(cli.app, ["demo", "--help"])

    assert held_help.exit_code == 0, held_help.output
    assert demo_help.exit_code == 0, demo_help.output


@pytest.mark.parametrize(
    ("path", "gate_name", "dependent_name", "expected_if"),
    [
        (
            WEEKLY,
            "provider-use-review",
            "authenticated-data",
            "github.event_name == 'workflow_dispatch' && inputs.mode == 'authenticated-data'",
        ),
        (
            PREWARM,
            "provider-use-review",
            "prewarm",
            "${{ github.ref == 'refs/heads/main' }}",
        ),
    ],
)
def test_manual_authenticated_workflows_fail_closed_without_credentials(
    tmp_path: Path,
    path: Path,
    gate_name: str,
    dependent_name: str,
    expected_if: str,
) -> None:
    document = _workflow(path)
    jobs = document["jobs"]
    gate = jobs[gate_name]
    assert gate["if"] == expected_if
    assert gate["permissions"] == {}
    assert "secrets." not in repr(gate)
    assert "continue-on-error" not in gate
    assert jobs[dependent_name]["needs"] == gate_name
    assert "always()" not in jobs[dependent_name].get("if", "")

    summary = tmp_path / "summary.md"
    step = gate["steps"][0]
    result = subprocess.run(
        ["/bin/bash", "-e", "-c", step["run"]],
        env={"GITHUB_STEP_SUMMARY": str(summary)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "issue #280" in summary.read_text(encoding="utf-8")


def test_weekly_synthetic_snapshot_dispatch_does_not_depend_on_provider_hold() -> None:
    jobs = _workflow(WEEKLY)["jobs"]
    assert "provider-use-review" not in jobs["demo-snapshot"].get("needs", [])
    assert jobs["demo-snapshot"]["if"] == (
        "github.event_name == 'schedule' || inputs.mode == 'demo'"
    )


def test_cli_hold_has_a_nonzero_exit_code_with_operator_guidance() -> None:
    result = CliRunner().invoke(cli.app, ["refresh-data"])

    assert result.exit_code != 0
    message = " ".join(result.output.split())
    assert "source-use review" in message and "#280" in message
