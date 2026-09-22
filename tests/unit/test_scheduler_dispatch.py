from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPT = Path("scripts/verify_scheduler_dispatch.py")
WORKFLOW_SCRIPT = Path("scripts/validate_scheduler_observation_workflow.py")
WORKFLOW = Path(".github/workflows/observe-forward-scheduler.yml")
NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)

_spec = importlib.util.spec_from_file_location("verify_scheduler_dispatch", SCRIPT)
assert _spec is not None and _spec.loader is not None
observer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(observer)


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": 35754822602,
        "name": "Forward production",
        "event": "workflow_dispatch",
        "path": ".github/workflows/forward-production.yml",
        "head_branch": "main",
        "head_sha": "a" * 40,
        "created_at": "2026-09-22T16:45:00Z",
        "run_started_at": "2026-09-22T16:45:04Z",
        "status": "completed",
        "conclusion": "success",
    }
    payload.update(overrides)
    return payload


def test_valid_dispatch_run_passes() -> None:
    assert (
        observer.validate_dispatch_run(
            _payload(),
            run_id="35754822602",
            now=NOW,
        )
        == ()
    )


def test_dispatch_validation_rejects_wrong_route_and_stale_time() -> None:
    reasons = observer.validate_dispatch_run(
        _payload(event="schedule", head_branch="feature", created_at="2026-09-22T15:00:00Z"),
        run_id="35754822602",
        now=NOW,
    )

    assert reasons == (
        "event_not_workflow_dispatch",
        "ref_mismatch",
        "created_at_stale",
    )


def test_dispatch_validation_rejects_future_and_invalid_fields() -> None:
    reasons = observer.validate_dispatch_run(
        _payload(
            id="99",
            path=".github/workflows/other.yml",
            head_sha="not-a-sha",
            created_at="2026-09-22T17:10:00Z",
            run_started_at="not-a-time",
            status="unknown",
            conclusion="secret-looking-value",
        ),
        run_id="35754822602",
        now=NOW,
    )

    assert reasons == (
        "payload_id_mismatch",
        "workflow_path_mismatch",
        "head_sha_invalid",
        "created_at_in_future",
        "status_invalid",
        "run_started_at_invalid",
        "conclusion_invalid",
    )


def test_redacted_report_ignores_unknown_values() -> None:
    payload = _payload(
        token="github-token-must-not-appear",
        response_body="private response content",
        nested={"credential": "do-not-copy"},
    )

    report = observer.build_redacted_report(
        payload,
        run_id="35754822602",
        checked_at=NOW,
        reason_codes=(),
    )

    encoded = json.dumps(report, sort_keys=True)
    assert "github-token-must-not-appear" not in encoded
    assert "private response content" not in encoded
    assert report["scheduler_origin"] == "unproven_by_github_run_metadata"
    assert report["status"] == "passed"


def test_fetch_rejects_missing_credentials_without_network() -> None:
    assert observer.fetch_github_run(repository="owner/repo", token="", run_id="42") == (
        None,
        ("github_token_missing",),
    )
    assert observer.fetch_github_run(
        repository="not a repository", token="secret", run_id="42"
    ) == (
        None,
        ("invalid_repository",),
    )


def test_cli_writes_passed_redacted_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "run.json"
    recent = datetime.now(UTC) - timedelta(minutes=1)
    fixture.write_text(
        json.dumps(_payload(created_at=recent.isoformat(), token="secret")),
        encoding="utf-8",
    )
    output = tmp_path / "reports" / "dispatch.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--run-id",
            "35754822602",
            "--run-json",
            str(fixture),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert "secret" not in output.read_text(encoding="utf-8")


def test_cli_retains_failed_report_for_stale_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "run.json"
    fixture.write_text(json.dumps(_payload(created_at="2020-01-01T00:00:00Z")), encoding="utf-8")
    output = tmp_path / "dispatch.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--run-id",
            "35754822602",
            "--run-json",
            str(fixture),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["reason_codes"] == ["created_at_stale"]


def test_observation_workflow_contract_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(WORKFLOW_SCRIPT), str(WORKFLOW)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "scheduler observation workflow contract passed" in result.stdout
