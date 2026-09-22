from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT = Path("scripts/measure_scheduler_lateness.py")
WORKFLOW_SCRIPT = Path("scripts/validate_scheduler_lateness_workflow.py")
WORKFLOW = Path(".github/workflows/measure-forward-scheduler.yml")
AS_OF = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)

_spec = importlib.util.spec_from_file_location("measure_scheduler_lateness", SCRIPT)
assert _spec is not None and _spec.loader is not None
measurement = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measurement)


def _run(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": 35754822602,
        "name": "Forward production",
        "event": "schedule",
        "path": ".github/workflows/forward-production.yml",
        "head_branch": "main",
        "head_sha": "a" * 40,
        "created_at": "2026-09-22T11:17:00Z",
        "run_started_at": "2026-09-22T11:20:00Z",
        "status": "completed",
        "conclusion": "success",
    }
    payload.update(overrides)
    return payload


def test_expected_schedule_at_handles_weekend_and_before_cron() -> None:
    assert measurement.expected_schedule_at(datetime(2026, 9, 22, 11, 17, tzinfo=UTC)) == datetime(
        2026, 9, 22, 7, 17, tzinfo=UTC
    )
    assert measurement.expected_schedule_at(datetime(2026, 9, 21, 6, 0, tzinfo=UTC)) == datetime(
        2026, 9, 19, 7, 17, tzinfo=UTC
    )


def test_report_computes_schedule_and_queue_delay_without_raw_values() -> None:
    report = measurement.build_lateness_report([_run()], as_of=AS_OF)

    assert report["status"] == "observed"
    assert report["scheduler_origin"] == "unproven_by_github_run_metadata"
    assert report["aggregate"]["schedule_delay_seconds"]["median"] == 4 * 60 * 60
    assert report["aggregate"]["queue_delay_seconds"]["median"] == 3 * 60
    assert report["aggregate"]["schedule_delay_warning_count"] == 1
    assert "Forward production" not in json.dumps(report)


def test_report_aggregates_in_scope_runs_and_excludes_old_runs() -> None:
    report = measurement.build_lateness_report(
        [
            _run(id=1, created_at="2026-09-22T07:17:00Z"),
            _run(id=2, created_at="2026-09-21T08:17:00Z"),
            _run(id=3, created_at="2026-08-01T08:17:00Z"),
        ],
        as_of=AS_OF,
        lookback_days=2,
    )

    assert report["status"] == "observed"
    assert report["source"] == {
        "returned_run_count": 3,
        "included_run_count": 2,
        "out_of_window_count": 1,
        "invalid_run_count": 0,
    }
    assert [item["run_id"] for item in report["observations"]] == [2, 1]


def test_invalid_metadata_blocks_and_never_echoes_unknown_values() -> None:
    report = measurement.build_lateness_report(
        [
            _run(
                event="workflow_dispatch",
                head_branch="feature",
                token="github-secret-must-not-appear",
                response_body="private payload",
            )
        ],
        as_of=AS_OF,
    )

    assert report["status"] == "blocked"
    assert report["reason_codes"] == ["invalid_run_metadata", "no_in_scope_runs"]
    invalid = report["invalid_observations"][0]
    assert invalid["reason_codes"] == ["event_not_schedule", "ref_mismatch"]
    encoded = json.dumps(report)
    assert "github-secret-must-not-appear" not in encoded
    assert "private payload" not in encoded


def test_source_error_values_are_reduced_to_stable_codes() -> None:
    report = measurement.build_lateness_report(
        [],
        as_of=AS_OF,
        source_errors=["safe_error", "secret-value-should-not-appear"],
    )

    assert report["reason_codes"] == ["safe_error", "source_error", "no_in_scope_runs"]
    assert "secret-value-should-not-appear" not in json.dumps(report)


def test_duplicate_run_ids_block_the_historical_measurement() -> None:
    report = measurement.build_lateness_report(
        [_run(), _run(created_at="2026-09-22T11:18:00Z")],
        as_of=AS_OF,
    )

    assert report["status"] == "blocked"
    assert report["source"]["invalid_run_count"] == 1
    assert report["invalid_observations"][0]["reason_codes"] == ["duplicate_run_id"]


def test_fetch_rejects_missing_credentials_without_network() -> None:
    assert measurement.fetch_scheduled_runs(repository="owner/repo", token="") == (
        [],
        ("github_token_missing",),
    )
    assert measurement.fetch_scheduled_runs(repository="not a repository", token="secret") == (
        [],
        ("invalid_repository",),
    )


def test_cli_writes_observed_fixture_report(tmp_path: Path) -> None:
    fixture = tmp_path / "runs.json"
    fixture.write_text(json.dumps({"workflow_runs": [_run()]}), encoding="utf-8")
    output = tmp_path / "reports" / "lateness.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--run-json",
            str(fixture),
            "--as-of",
            AS_OF.isoformat(),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "observed"


def test_lateness_workflow_contract_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(WORKFLOW_SCRIPT), str(WORKFLOW)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "scheduler lateness workflow contract passed" in result.stdout


def test_lateness_workflow_contract_rejects_repository_secret_access(tmp_path: Path) -> None:
    workflow = tmp_path / "unsafe.yml"
    workflow.write_text(
        WORKFLOW.read_text(encoding="utf-8").replace("github.token", "secrets.GITHUB_TOKEN"),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(WORKFLOW_SCRIPT), str(workflow)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "validation error(s) detected" in result.stderr
