"""In-process tests for the Typer CLI, which subprocess smoke checks cannot cover."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import orjson
import pytest
import typer
from typer.testing import CliRunner

from edgar_moe import cli
from edgar_moe.copilot.agent import OpenAICompatibleProvider, ProviderResponse, ProviderToolCall
from edgar_moe.settings import RuntimeSettings, runtime_settings

runner = CliRunner()


def test_validate_config_reports_each_protocols_fact_policy() -> None:
    frozen = runner.invoke(cli.app, ["validate-config", "--config", "config/authenticated-free.yaml"])
    current = runner.invoke(cli.app, ["validate-config", "--config", "config/authenticated-v2.yaml"])

    assert frozen.exit_code == 0 and current.exit_code == 0
    assert json.loads(frozen.stdout)["features"]["xbrl_fact_policy"] == "legacy_v1"
    assert json.loads(current.stdout)["features"]["xbrl_fact_policy"] == "duration_aware_v2"


def test_demo_refuses_to_replace_the_locked_public_snapshot(tmp_path: Path) -> None:
    locked = tmp_path / "snapshot.json"
    shutil.copyfile("data/demo/snapshot.json", locked)
    digest = hashlib.sha256(locked.read_bytes()).hexdigest()

    result = runner.invoke(cli.app, ["demo", "--output", str(locked), "--epochs", "1"])

    assert result.exit_code != 0
    assert isinstance(result.exception, FileExistsError)
    assert hashlib.sha256(locked.read_bytes()).hexdigest() == digest


def test_forward_init_migrates_then_status_reports_an_empty_registry(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'registry.sqlite3'}"

    initialized = runner.invoke(cli.app, ["forward-init", "--database-url", url])
    status = runner.invoke(cli.app, ["forward-status", "--database-url", url])

    assert initialized.exit_code == 0, initialized.output
    assert status.exit_code == 0, status.output
    payload = json.loads(status.stdout)
    assert payload["status"]["configured"] is True
    assert payload["status"]["run_count"] == 0
    assert payload["status"]["health_status"] == "degraded"
    assert payload["performance"]["forecast_count"] == 0


def test_copilot_plan_only_describes_the_tools_without_contacting_a_provider() -> None:
    result = runner.invoke(cli.app, ["research-copilot", "What was measured?", "--plan-only"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["provider_contacted"] is False
    assert len(report["tools"]) == 7
    snapshot_digest = hashlib.sha256(Path("data/demo/snapshot.json").read_bytes()).hexdigest()
    assert report["frozen_identity"]["sha256"] == snapshot_digest


def _copy_locked_repository(root: Path) -> Path:
    (root / "config").mkdir(parents=True)
    (root / "data" / "demo").mkdir(parents=True)
    shutil.copyfile("config/public_snapshot.lock.json", root / "config/public_snapshot.lock.json")
    snapshot = root / "data/demo/snapshot.json"
    shutil.copyfile("data/demo/snapshot.json", snapshot)
    return snapshot


def test_copilot_refuses_a_public_snapshot_that_drifted_from_its_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _copy_locked_repository(tmp_path)
    monkeypatch.setenv("EDGAR_MOE_DEMO_SNAPSHOT", str(snapshot))
    monkeypatch.setenv("EDGAR_MOE_PUBLIC_SNAPSHOT_LOCK", str(tmp_path / "config/public_snapshot.lock.json"))
    runtime_settings.cache_clear()

    verified = runner.invoke(cli.app, ["research-copilot", "q", "--plan-only", "--snapshot", str(snapshot)])
    snapshot.write_bytes(snapshot.read_bytes().replace(b'"research_only": true', b'"research_only": true '))
    tampered = runner.invoke(cli.app, ["research-copilot", "q", "--plan-only", "--snapshot", str(snapshot)])

    assert verified.exit_code == 0, verified.output
    assert tampered.exit_code != 0
    assert "do not match their lock" in str(tampered.exception)


def test_copilot_answer_is_written_and_verifiable_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = [
        ProviderResponse(
            content="",
            tool_calls=(ProviderToolCall("call-1", "get_study_summary", "{}"),),
            model="fake-model",
        ),
        ProviderResponse(
            content="The frozen study summary is cited; this is research only.",
            tool_calls=(),
            model="fake-model",
        ),
    ]

    def fake_complete(self: OpenAICompatibleProvider, messages: object, tools: object) -> ProviderResponse:
        del self, messages, tools
        return responses.pop(0)

    monkeypatch.setattr(OpenAICompatibleProvider, "complete", fake_complete)
    output = tmp_path / "answer.json"

    asked = runner.invoke(cli.app, ["research-copilot", "What does the study report?", "--output", str(output)])
    verified = runner.invoke(cli.app, ["research-copilot-verify", str(output)])

    assert asked.exit_code == 0, asked.output
    envelope = orjson.loads(output.read_bytes())
    assert envelope["evidence_status"] == "grounded"
    assert verified.exit_code == 0, verified.output
    assert "citations=1; tool_calls=1" in verified.stdout


def test_copilot_verify_rejects_an_invalid_envelope(tmp_path: Path) -> None:
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")

    result = runner.invoke(cli.app, ["research-copilot-verify", str(answer)])

    assert result.exit_code == 2
    assert "missing fields" in result.output


def test_diagnostic_history_verify_rejects_unreadable_input(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["forward-diagnostic-history-verify", str(tmp_path / "missing.json")])

    assert result.exit_code == 2


def _settings(**values: str) -> RuntimeSettings:
    return RuntimeSettings.model_validate(values)


def test_forward_database_url_prefers_the_override_then_settings_then_local_sqlite() -> None:
    assert cli._forward_database_url(_settings(), "sqlite:///override.db") == "sqlite:///override.db"
    configured = _settings(edgar_moe_registry_database_url="postgresql://writer")
    assert cli._forward_database_url(configured, None) == "postgresql://writer"
    assert cli._forward_database_url(_settings(), None) == "sqlite:///data/forward/registry.sqlite3"


def test_copilot_database_url_never_falls_back_to_a_postgres_writer() -> None:
    writer_only = _settings(edgar_moe_registry_database_url="postgresql://writer")
    assert cli._copilot_database_url(writer_only, None) == ""
    reader = _settings(
        edgar_moe_registry_database_url="postgresql://writer",
        edgar_moe_registry_read_database_url="postgresql://reader",
    )
    assert cli._copilot_database_url(reader, None) == "postgresql://reader"
    local = _settings(edgar_moe_registry_database_url="sqlite:///local.db")
    assert cli._copilot_database_url(local, None) == "sqlite:///local.db"
    assert cli._copilot_database_url(writer_only, "sqlite:///cli.db") == "sqlite:///cli.db"


def test_timestamps_must_be_timezone_aware_and_are_normalized_to_utc() -> None:
    parsed = cli._parse_timestamp("2026-09-21T03:17:00-04:00")
    assert parsed == datetime(2026, 9, 21, 7, 17, tzinfo=UTC)
    assert cli._parse_timestamp(None).tzinfo is not None
    with pytest.raises(typer.BadParameter, match="timezone"):
        cli._parse_timestamp("2026-09-21T03:17:00")
    with pytest.raises(typer.BadParameter, match="ISO-8601"):
        cli._parse_timestamp("yesterday")


def test_database_labels_never_echo_postgres_credentials() -> None:
    assert cli._safe_database_label("postgresql://user:secret@host/db") == "configured Postgres database"
    assert cli._safe_database_label("sqlite:///local.db") == "sqlite:///local.db"
