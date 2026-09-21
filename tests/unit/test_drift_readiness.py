from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

import orjson
import pytest

from edgar_moe.features.drift_history import build_research_drift_history
from edgar_moe.features.drift_readiness import (
    DriftReadinessError,
    build_research_drift_readiness,
    verify_research_drift_readiness,
)


def _report(dataset_id: str, as_of: str, *, status: str = "stable") -> dict[str, Any]:
    report: dict[str, Any] = {
        "report_version": 1,
        "scope": "research_drift_only",
        "v1_immutable": True,
        "automatic_retraining": False,
        "service_health_untouched": True,
        "status": status,
        "baseline": {
            "dataset_id": "training-2025-01-01",
            "as_of": "2025-01-01T00:00:00+00:00",
            "source_manifest_hash": "baseline-source-hash",
            "event_count": 10,
            "included_events": 10,
        },
        "prospective": {
            "dataset_id": dataset_id,
            "as_of": as_of,
            "source_manifest_hash": f"{dataset_id}-source",
            "event_count": 12,
            "included_events": 12,
        },
        "context": {
            "model_id": "edgar-moe-frozen-v1",
            "model_version": "1.0.0-frozen",
            "artifact_sha256": "a" * 64,
            "selection_hash": "b" * 64,
            "frozen_at": "2025-01-01T00:00:00+00:00",
        },
        "temporal_order": "later_or_same_as_of",
        "thresholds": {
            "missing_rate_delta": 0.1,
            "standardized_mean_shift": 1.0,
            "population_stability_index": 0.2,
        },
        "features": {},
        "component_outputs": {
            "status": "not_supplied",
            "source": "not_supplied",
            "components": [],
        },
        "summary": {
            "feature_count": 0,
            "component_count": 0,
            "feature_warning_count": 1 if status == "warning" else 0,
            "component_warning_count": 0,
            "warning_names": ["market_a"] if status == "warning" else [],
            "dimension_mismatch_modalities": [],
        },
    }
    report["report_hash"] = sha256(orjson.dumps(report, option=orjson.OPT_SORT_KEYS)).hexdigest()
    return report


def _history(*, status: str = "stable", count: int = 3) -> dict[str, Any]:
    reports = [
        _report(
            f"prospective-{index}",
            f"2025-{index + 1:02d}-01T00:00:00+00:00",
            status=status,
        )
        for index in range(count)
    ]
    return build_research_drift_history(reports, minimum_reports=3)


def test_readiness_is_ready_for_three_stable_observations() -> None:
    history = _history()

    report = build_research_drift_readiness(history)

    assert report["status"] == "ready"
    assert report["report_count"] == 3
    assert report["blocked_reasons"] == []
    assert "observations" not in report
    verify_research_drift_readiness(report)


def test_readiness_blocks_insufficient_history() -> None:
    history = _history(count=2)

    report = build_research_drift_readiness(history)

    assert report["status"] == "blocked"
    assert report["blocked_reasons"] == [
        "minimum_observations",
        "history_contract_minimum",
        "history_status",
        "review_required",
    ]


def test_readiness_requires_human_review_for_warning_history() -> None:
    history = _history(status="warning")

    report = build_research_drift_readiness(history)

    assert report["status"] == "review_required"
    assert report["blocked_reasons"] == [
        "history_status",
        "review_required",
        "warning_streak",
    ]


def test_readiness_blocks_errors_and_custom_minimums() -> None:
    error_history = _history(status="error")
    error_report = build_research_drift_readiness(error_history)
    assert error_report["status"] == "blocked"

    stable_history = _history()
    minimum_report = build_research_drift_readiness(stable_history, minimum_reports=4)
    assert minimum_report["status"] == "blocked"
    assert minimum_report["blocked_reasons"] == ["minimum_observations"]


def test_readiness_rejects_tampering_and_invalid_history() -> None:
    history = _history()
    report = build_research_drift_readiness(history)
    tampered = deepcopy(report)
    tampered["status"] = "blocked"

    with pytest.raises(DriftReadinessError, match="status is inconsistent"):
        verify_research_drift_readiness(tampered)

    invalid_history = deepcopy(history)
    invalid_history["report_count"] = 1
    with pytest.raises(DriftReadinessError, match="drift history"):
        build_research_drift_readiness(invalid_history)
