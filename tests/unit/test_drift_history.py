from __future__ import annotations

from hashlib import sha256
from typing import Any

import orjson
import pytest

from edgar_moe.features.drift_history import (
    DriftHistoryError,
    build_research_drift_history,
    verify_research_drift_history,
)


def make_report(
    dataset_id: str,
    as_of: str,
    *,
    status: str = "stable",
    model_version: str = "1.0.0-frozen",
) -> dict[str, Any]:
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
            "model_version": model_version,
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
        "component_outputs": {"status": "not_supplied", "source": "not_supplied", "components": []},
        "summary": {
            "feature_count": 0,
            "component_count": 0,
            "feature_warning_count": 1 if status == "warning" else 0,
            "component_warning_count": 0,
            "warning_names": ["market_a"] if status == "warning" else [],
            "dimension_mismatch_modalities": [],
        },
    }
    report["report_hash"] = sha256(
        orjson.dumps(report, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    return report


def test_history_requires_minimum_observations_and_preserves_hashes() -> None:
    reports = [
        make_report("prospective-1", "2025-02-01T00:00:00+00:00"),
        make_report("prospective-2", "2025-03-01T00:00:00+00:00"),
    ]

    history = build_research_drift_history(
        reports,
        report_paths=["reports/one.json", "reports/two.json"],
    )

    assert history["status"] == "insufficient_history"
    assert history["review_required"] is True
    assert history["report_count"] == 2
    assert [item["sequence"] for item in history["observations"]] == [1, 2]
    assert history["report_files"][0]["path"] == "reports/one.json"
    verify_research_drift_history(history)
    history["status"] = "stable"
    with pytest.raises(DriftHistoryError, match="content hash mismatch"):
        verify_research_drift_history(history)


def test_history_orders_reports_and_tracks_warning_streak() -> None:
    reports = [
        make_report("prospective-3", "2025-04-01T00:00:00+00:00", status="warning"),
        make_report("prospective-1", "2025-02-01T00:00:00+00:00", status="warning"),
        make_report("prospective-2", "2025-03-01T00:00:00+00:00", status="warning"),
    ]

    history = build_research_drift_history(reports, minimum_reports=3)

    assert history["status"] == "warning"
    assert history["warning_streak"] == 3
    assert [
        item["prospective"]["dataset_id"] for item in history["observations"]
    ] == ["prospective-1", "prospective-2", "prospective-3"]


def test_history_rejects_identity_threshold_and_duplicate_drift() -> None:
    baseline = make_report("prospective-1", "2025-02-01T00:00:00+00:00")
    changed_model = make_report(
        "prospective-2", "2025-03-01T00:00:00+00:00", model_version="2.0.0"
    )
    with pytest.raises(DriftHistoryError, match="model identity"):
        build_research_drift_history([baseline, changed_model])

    duplicate = make_report("prospective-1", "2025-03-01T00:00:00+00:00")
    with pytest.raises(DriftHistoryError, match="duplicate"):
        build_research_drift_history([baseline, duplicate])

    changed_thresholds = make_report("prospective-2", "2025-03-01T00:00:00+00:00")
    changed_thresholds["thresholds"]["population_stability_index"] = 0.3
    changed_thresholds["report_hash"] = sha256(
        orjson.dumps(
            {key: value for key, value in changed_thresholds.items() if key != "report_hash"},
            option=orjson.OPT_SORT_KEYS,
        )
    ).hexdigest()
    with pytest.raises(DriftHistoryError, match="thresholds"):
        build_research_drift_history([baseline, changed_thresholds])


def test_history_rejects_naive_timestamps() -> None:
    report = make_report("prospective-1", "2025-02-01T00:00:00")
    with pytest.raises(DriftHistoryError, match="timezone-naive"):
        build_research_drift_history([report])
