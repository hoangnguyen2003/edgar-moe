from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from edgar_moe.cli import (
    _require_synthetic_copilot_benchmark_context,
    _require_synthetic_copilot_provider_credentials,
    app,
)
from edgar_moe.copilot.benchmark import (
    ensure_private_output_outside_git,
    write_benchmark_report,
)
from edgar_moe.copilot.blind_review import write_private_json
from edgar_moe.settings import RuntimeSettings


def _settings(**overrides: object) -> RuntimeSettings:
    values: dict[str, object] = {
        "edgar_moe_demo_snapshot": Path("data/demo/snapshot.json"),
        "edgar_moe_public_snapshot_lock": Path("config/public_snapshot.lock.json"),
        "edgar_moe_registry_database_url": "",
        "edgar_moe_registry_read_database_url": "",
    }
    values.update(overrides)
    return RuntimeSettings(_env_file=None, **values)


def _require_context(
    *,
    settings: RuntimeSettings | None = None,
    snapshot: Path = Path("data/demo/snapshot.json"),
    database_url: str | None = None,
    diagnostic_path: Path | None = None,
    diagnostic_history_path: Path | None = None,
) -> None:
    _require_synthetic_copilot_benchmark_context(
        settings or _settings(),
        snapshot=snapshot,
        database_url=database_url,
        diagnostic_path=diagnostic_path,
        diagnostic_history_path=diagnostic_history_path,
    )


def test_synthetic_context_accepts_only_locked_fixture_without_external_evidence() -> None:
    _require_context()


def test_synthetic_provider_requires_authentication_for_remote_endpoint() -> None:
    with pytest.raises(typer.BadParameter, match="requires a provider API key"):
        _require_synthetic_copilot_provider_credentials(
            endpoint="https://api.openai.com/v1/chat/completions",
            api_key_configured=False,
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434/v1/chat/completions",
        "http://127.0.0.1:1234/v1/chat/completions",
        "http://[::1]:11434/v1/chat/completions",
    ],
)
def test_synthetic_provider_allows_loopback_runtime_without_key(endpoint: str) -> None:
    _require_synthetic_copilot_provider_credentials(endpoint=endpoint, api_key_configured=False)


def test_synthetic_provider_allows_authenticated_remote_endpoint() -> None:
    _require_synthetic_copilot_provider_credentials(
        endpoint="https://api.openai.com/v1/chat/completions",
        api_key_configured=True,
    )


def test_private_copilot_outputs_are_rejected_inside_git_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / "synthetic-worktree"
    (worktree / ".git").mkdir(parents=True)
    private_dir = worktree / "data" / "artifacts" / "private-output"

    with pytest.raises(PermissionError, match="outside a Git working tree"):
        ensure_private_output_outside_git(private_dir)
    with pytest.raises(PermissionError, match="outside a Git working tree"):
        write_benchmark_report(private_dir / "evaluation.json", {"safe": True})
    with pytest.raises(PermissionError, match="outside a Git working tree"):
        write_private_json(private_dir / "packet.json", {"private": True})

    assert not private_dir.exists()


def test_masked_review_cli_refuses_git_output_before_reading_inputs(tmp_path: Path) -> None:
    worktree = tmp_path / "synthetic-worktree"
    (worktree / ".git").mkdir(parents=True)
    output_dir = worktree / "data" / "artifacts" / "masked-review-output"
    result = CliRunner().invoke(
        app,
        [
            "research-copilot-mask-review",
            "--baseline-dir",
            str(tmp_path / "missing-baseline"),
            "--copilot-dir",
            str(tmp_path / "missing-copilot"),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code != 0
    assert "private copilot output must be outside a Git working tree" in result.output
    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("settings", "snapshot", "database_url", "diagnostic_path", "history_path", "message"),
    [
        (
            None,
            Path("data/interim/other.json"),
            None,
            None,
            None,
            "exact lock-verified",
        ),
        (
            _settings(edgar_moe_public_snapshot_lock=Path("config/other-lock.json")),
            Path("data/demo/snapshot.json"),
            None,
            None,
            None,
            "exact lock-verified",
        ),
        (
            None,
            Path("data/demo/snapshot.json"),
            "postgresql://unused",
            None,
            None,
            "forbids registry",
        ),
        (
            _settings(edgar_moe_registry_read_database_url="postgresql://configured-reader"),
            Path("data/demo/snapshot.json"),
            None,
            None,
            None,
            "forbids registry",
        ),
        (
            _settings(edgar_moe_registry_database_url="sqlite:///data/forward/registry.sqlite3"),
            Path("data/demo/snapshot.json"),
            None,
            None,
            None,
            "forbids registry",
        ),
        (
            None,
            Path("data/demo/snapshot.json"),
            None,
            Path("private-diagnostics.json"),
            None,
            "forbids diagnostic",
        ),
        (
            None,
            Path("data/demo/snapshot.json"),
            None,
            None,
            Path("private-history.json"),
            "forbids diagnostic",
        ),
    ],
)
def test_synthetic_context_rejects_unmatched_inputs(
    settings: RuntimeSettings | None,
    snapshot: Path,
    database_url: str | None,
    diagnostic_path: Path | None,
    history_path: Path | None,
    message: str,
) -> None:
    with pytest.raises(typer.BadParameter, match=message) as error:
        _require_context(
            settings=settings,
            snapshot=snapshot,
            database_url=database_url,
            diagnostic_path=diagnostic_path,
            diagnostic_history_path=history_path,
        )
    assert "postgresql://" not in str(error.value)


def test_benchmark_help_explains_synthetic_only_still_contacts_provider() -> None:
    result = CliRunner().invoke(app, ["research-copilot-benchmark", "--help"])

    assert result.exit_code == 0, result.output
    assert "--synthetic-only" in result.output
    assert "still contacts" in result.output
    assert "configured" in result.output
    assert "provider." in result.output


def test_synthetic_only_cli_rejects_database_override_before_creating_outputs(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "must-not-be-created"
    result = CliRunner().invoke(
        app,
        [
            "research-copilot-benchmark",
            "--experimental-llm",
            "--synthetic-only",
            "--database-url",
            "postgresql://unused",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code != 0
    assert "--synthetic-only forbids registry database context" in result.output
    assert "postgresql://" not in result.output
    assert not output_dir.exists()


def test_synthetic_only_cli_rejects_missing_remote_key_before_creating_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "must-not-be-created"
    monkeypatch.setattr("edgar_moe.cli.runtime_settings", lambda: _settings())
    result = CliRunner().invoke(
        app,
        [
            "research-copilot-benchmark",
            "--experimental-llm",
            "--synthetic-only",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code != 0
    assert "requires a provider API key" in result.output
    assert not output_dir.exists()
