from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from edgar_moe.forward.restore_reports import (
    RestoreComparisonError,
    compare_reports,
    verify_restore_comparison_report,
)


def audit(*findings: dict[str, str], status: str = "passed") -> dict[str, object]:
    return {"status": status, "findings": list(findings)}


def test_restore_comparison_accepts_matching_counts_and_baseline_findings() -> None:
    baseline = {"code": "failed_run", "run_id": "run-1"}
    result = compare_reports(
        {"forward_runs": 3},
        {"forward_runs": 3},
        audit(baseline),
        audit(baseline),
    )

    assert result["status"] == "passed"
    assert result["counts_match"] is True
    assert result["new_findings"] == []
    assert len(result["report_hash"]) == 64
    verify_restore_comparison_report(result)


def test_restore_comparison_rejects_count_drift_and_new_findings() -> None:
    result = compare_reports(
        {"forward_runs": 3},
        {"forward_runs": 4},
        audit(),
        audit({"code": "hash_mismatch", "artifact_id": "artifact-1"}),
    )

    assert result["status"] == "failed"
    assert result["counts_match"] is False
    assert len(result["new_findings"]) == 1
    verify_restore_comparison_report(result)


def test_restore_comparison_rejects_incomplete_audit() -> None:
    result = compare_reports(
        {"forward_runs": 3},
        {"forward_runs": 3},
        audit(status="incomplete"),
        audit(status="passed"),
    )

    assert result["status"] == "failed"


def test_restore_comparison_hash_rejects_tampering() -> None:
    result = compare_reports(
        {"forward_runs": 3},
        {"forward_runs": 3},
        audit(),
        audit(),
    )
    result["status"] = "failed"

    with pytest.raises(RestoreComparisonError, match="does not match"):
        verify_restore_comparison_report(result)


def test_restore_comparison_verifier_cli_checks_retained_report(tmp_path: Path) -> None:
    report = compare_reports(
        {"forward_runs": 3},
        {"forward_runs": 3},
        audit(),
        audit(),
    )
    report_path = tmp_path / "restore-comparison.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/verify_restore_comparison.py",
            "--report",
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    output = json.loads(result.stdout)
    assert output["status"] == "verified"
    assert output["report_hash"] == report["report_hash"]
