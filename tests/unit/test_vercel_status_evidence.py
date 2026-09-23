from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path("scripts/write_vercel_status_failure.py")
WORKFLOW = Path(".github/workflows/vercel-status-evidence.yml")
SPEC = importlib.util.spec_from_file_location("write_vercel_status_failure", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SHA = "a" * 40


def event(**changes: object) -> dict[str, object]:
    payload = {
        "sha": SHA,
        "context": "Vercel",
        "state": "failure",
        "id": 42,
        "description": "token=do-not-retain",
        "target_url": "https://example.vercel.app/build?token=do-not-retain",
        "sender": {"login": "do-not-retain"},
    }
    payload.update(changes)
    return payload


def test_report_keeps_only_allowlisted_status_fields() -> None:
    report = MODULE.build_report(event(), default_sha=SHA, checked_at="2026-09-23T03:24:00Z")

    assert report["status"] == "vercel_commit_status_failed"
    assert report["commit_sha"] == SHA
    assert report["state"] == "failure"
    assert report["production_deployment_verified"] is False
    serialized = json.dumps(report)
    assert "do-not-retain" not in serialized
    assert "target_url" not in serialized
    assert "description" not in serialized


def test_cli_retains_redacted_report(tmp_path: Path) -> None:
    source = tmp_path / "status-event.json"
    output = tmp_path / "vercel-status-failure.json"
    source.write_text(json.dumps(event()), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--event",
            str(source),
            "--default-sha",
            SHA,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["commit_sha"] == SHA
    assert "do-not-retain" not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("changes", "default_sha"),
    [
        ({"sha": "b" * 40}, SHA),
        ({"sha": "short"}, SHA),
        ({"context": "Other"}, SHA),
        ({"state": "pending"}, SHA),
        ({"state": "success"}, SHA),
        ({"id": 0}, SHA),
        ({"id": True}, SHA),
        ({}, "short"),
    ],
)
def test_report_rejects_nonmatching_or_nonterminal_statuses(
    changes: dict[str, object], default_sha: str
) -> None:
    with pytest.raises(ValueError):
        MODULE.build_report(event(**changes), default_sha=default_sha)


def test_workflow_restricts_and_retains_without_deployment_authority() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "  status:" in text
    assert "  contents: read" in text
    assert "github.event.context == 'Vercel'" in text
    assert "github.event.sha == github.sha" in text
    assert "github.event.state == 'failure'" in text
    assert "github.event.state == 'error'" in text
    assert "retention-days: 30" in text
    assert "scripts/write_vercel_status_failure.py" in text
    assert "scripts/validate_redacted_artifacts.py --root reports/deployment-smoke" in text
    assert '--event "$GITHUB_EVENT_PATH"' in text
    assert "secrets." not in text
