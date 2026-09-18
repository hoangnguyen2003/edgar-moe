from __future__ import annotations

from edgar_moe.forward.restore_reports import compare_reports


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


def test_restore_comparison_rejects_incomplete_audit() -> None:
    result = compare_reports(
        {"forward_runs": 3},
        {"forward_runs": 3},
        audit(status="incomplete"),
        audit(status="passed"),
    )

    assert result["status"] == "failed"
