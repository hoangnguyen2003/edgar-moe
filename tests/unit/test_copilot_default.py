"""Default navigation never enables a model or registry from local settings."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from edgar_moe import cli
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


def test_default_uses_unchanged_deterministic_navigation(
    no_provider_or_database: None,
) -> None:
    result = CliRunner().invoke(cli.app, ["research-copilot", "Show the study summary."])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    verify_copilot_answer_report(report)
    assert report["provider"] == "none"
    assert report["model"] == "deterministic-evidence-navigation-v1"
    assert report["tool_trace"][0]["name"] == "get_study_summary"
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
    options: list[str], tmp_path: Path, no_provider_or_database: None
) -> None:
    output = tmp_path / "must-not-be-created.json"
    result = CliRunner().invoke(
        cli.app, ["research-copilot", "Show summary.", *options, "--output", str(output)]
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
