"""Default navigation never enables a model or registry from local settings."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from edgar_moe import cli
from edgar_moe.copilot.navigation import NAVIGATOR_TOOL_NAMES, NAVIGATOR_VERSION
from edgar_moe.copilot.presentation import render_copilot_answer_text
from edgar_moe.copilot.verification import verify_copilot_answer_report


def _unexpected_call(*args: object, **kwargs: object) -> None:
    raise AssertionError("default navigation must not construct a provider or database")


@pytest.fixture
def no_provider_or_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("edgar_moe.copilot.OpenAICompatibleProvider", _unexpected_call)
    monkeypatch.setattr("edgar_moe.forward.database.RegistryDatabase", _unexpected_call)
    monkeypatch.setenv("EDGAR_MOE_COPILOT_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("EDGAR_MOE_REGISTRY_READ_DATABASE_URL", "postgresql://unused-reader")
    monkeypatch.setenv("EDGAR_MOE_REGISTRY_DATABASE_URL", "sqlite:///must-not-open.db")


def test_default_uses_versioned_deterministic_explanations(
    no_provider_or_database: None,
) -> None:
    result = CliRunner().invoke(cli.app, ["research-copilot", "Show the study summary."])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    verify_copilot_answer_report(report)
    assert report["provider"] == "none"
    assert report["model"] == "deterministic-evidence-explanations-v1"
    assert report["tool_trace"][0]["name"] == "get_study_summary"
    assert "Synthetic demonstration only" in report["answer"]
    assert "Prediction metrics alone do not establish" in report["answer"]
    assert "usage" not in report
    assert "agent_identity" not in report


def test_default_retains_safe_refusal_and_abstention(no_provider_or_database: None) -> None:
    for question in ("Buy shares using the portfolio metrics.", "An undocumented subject."):
        result = CliRunner().invoke(cli.app, ["research-copilot", question])
        assert result.exit_code == 0, result.output
        report = json.loads(result.stdout)
        verify_copilot_answer_report(report)
        assert report["provider"] == "none"
        assert report["evidence_status"] == "uncited"
        assert not report["tool_trace"]


@pytest.mark.parametrize(
    "question",
    [
        "Explain target, metrics and pending governance.",
        "Buy shares using the summary.",
        "Give the live stock price.",
        "An undocumented subject.",
    ],
)
def test_readable_format_uses_the_same_verified_answer_without_provider_or_database(
    no_provider_or_database: None, question: str
) -> None:
    runner = CliRunner()
    raw = runner.invoke(cli.app, ["research-copilot", question])
    readable = runner.invoke(cli.app, ["research-copilot", question, "--format", "text"])
    assert raw.exit_code == readable.exit_code == 0, readable.output
    report = json.loads(raw.stdout)
    assert readable.stdout == render_copilot_answer_text(report) + "\n"
    assert "not a factuality score" in readable.stdout
    assert report["disclaimer"] in readable.stdout


@pytest.mark.parametrize(
    "options",
    [
        ["--format", "html"],
        ["--format", "TEXT"],
        ["--format", ""],
        ["--format", "text", "--plan-only"],
        ["--format", "text", "--plan-only", "--experimental-llm"],
    ],
)
def test_invalid_presentation_options_fail_before_settings_or_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: list[str]
) -> None:
    monkeypatch.setattr(cli, "runtime_settings", _unexpected_call)
    output = tmp_path / "must-not-exist.json"
    result = CliRunner().invoke(
        cli.app, ["research-copilot", "Show summary", *options, "--output", str(output)]
    )
    assert result.exit_code == 2, result.output
    assert "--format" in result.output
    assert not output.exists()


def test_default_preview_describes_only_the_actual_snapshot_capabilities(
    no_provider_or_database: None,
) -> None:
    result = CliRunner().invoke(cli.app, ["research-copilot", "Show summary", "--plan-only"])
    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)
    assert plan["execution_mode"] == "deterministic"
    assert plan["navigator_version"] == NAVIGATOR_VERSION
    assert plan["max_tool_calls"] == len(NAVIGATOR_TOOL_NAMES) == 5
    assert [tool["function"]["name"] for tool in plan["tools"]] == list(NAVIGATOR_TOOL_NAMES)
    for tool in plan["tools"]:
        assert tool["function"]["parameters"] == {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }


def test_opted_in_experimental_preview_does_not_open_attached_capabilities(
    no_provider_or_database: None,
) -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "research-copilot",
            "Show summary",
            "--plan-only",
            "--experimental-llm",
            "--profile",
            "architect",
            "--database-url",
            "postgresql://unused",
            "--diagnostic-path",
            "must-not-read.json",
            "--diagnostic-history-path",
            "must-not-read-history.json",
        ],
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)
    assert plan["provider_contacted"] is False
    assert plan["profile_id"] == "architect"
    assert plan["execution_mode"] == "experimental_llm"
    assert len(plan["tools"]) == 9
    assert "navigator_version" not in plan
    assert "max_tool_calls" not in plan


def test_default_private_output_is_owner_only(
    tmp_path: Path, no_provider_or_database: None
) -> None:
    output = tmp_path / "private" / "answer.json"
    result = CliRunner().invoke(
        cli.app, ["research-copilot", "Show methodology limitations.", "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert "Direct read-only evidence" not in result.output
    verify_copilot_answer_report(json.loads(output.read_text()))
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700


def test_text_format_private_output_still_saves_json_and_does_not_echo_answer(
    tmp_path: Path, no_provider_or_database: None
) -> None:
    output = tmp_path / "private" / "answer.json"
    result = CliRunner().invoke(
        cli.app,
        [
            "research-copilot",
            "Show target limitations",
            "--format",
            "text",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text())
    verify_copilot_answer_report(report)
    assert report["model"] == NAVIGATOR_VERSION
    assert result.stdout == f"Wrote private research-copilot report to {output}\n"
    assert report["answer"] not in result.stdout
    assert "Source target:" not in result.stdout
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("planning", [False, True])
@pytest.mark.parametrize(
    "options",
    [
        ["--endpoint", "http://127.0.0.1:1/v1/chat/completions"],
        ["--model", "test-model"],
        ["--profile", "quant"],
        ["--database-url", "postgresql://unused"],
        ["--diagnostic-path", "missing.json"],
        ["--diagnostic-history-path", "missing.json"],
        ["--max-tool-calls", "2"],
        ["--max-duration-seconds", "20"],
        ["--max-context-bytes", "16384"],
        ["--max-retries", "0"],
        ["--retry-backoff-seconds", "0"],
    ],
)
def test_advanced_options_do_not_implicitly_enable_llm(
    options: list[str], tmp_path: Path, no_provider_or_database: None, planning: bool
) -> None:
    output = tmp_path / "must-not-be-created.json"
    result = CliRunner().invoke(
        cli.app,
        [
            "research-copilot",
            "Show summary.",
            *options,
            *(["--plan-only"] if planning else []),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 2, result.output
    assert "--experimental-llm" in result.output
    assert not output.exists()


@pytest.mark.parametrize(
    "command",
    [["research-copilot-panel", "Show summary."], ["research-copilot-benchmark"]],
)
def test_provider_exercises_require_opt_in_before_settings_or_outputs(
    command: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "runtime_settings", _unexpected_call)
    output = tmp_path / "must-not-be-created"
    result = CliRunner().invoke(cli.app, [*command, "--output-dir", str(output)])
    assert result.exit_code == 2, result.output
    assert "--experimental-llm" in result.output
    assert not output.exists()


@pytest.mark.parametrize(
    "command",
    [
        ["research-copilot", "Show summary.", "--experimental-llm"],
        ["research-copilot-panel", "Show summary."],
        ["research-copilot-benchmark"],
    ],
)
def test_plan_only_remains_provider_and_database_free(
    command: list[str], no_provider_or_database: None
) -> None:
    result = CliRunner().invoke(cli.app, [*command, "--plan-only"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["provider_contacted"] is False
