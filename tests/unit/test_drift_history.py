from __future__ import annotations

from copy import deepcopy
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


def rehash_history(history: dict[str, Any]) -> dict[str, Any]:
    unsigned = {key: value for key, value in history.items() if key != "history_hash"}
    history["history_hash"] = sha256(
        orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    return history


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
    with pytest.raises(DriftHistoryError, match="status does not match"):
        verify_research_drift_history(history)

    history = build_research_drift_history(
        reports,
        report_paths=["reports/one.json", "reports/two.json"],
    )
    history["observed_at"] = "2025-04-01T00:00:00+00:00"
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


def test_history_rejects_rehashed_invariant_mutations() -> None:
    reports = [
        make_report("prospective-1", "2025-02-01T00:00:00+00:00"),
        make_report("prospective-2", "2025-03-01T00:00:00+00:00"),
    ]
    mutations = (
        (
            "unknown fields",
            lambda history: history.__setitem__("operator_note", "private"),
        ),
        (
            "report_count",
            lambda history: history.__setitem__("report_count", 1),
        ),
        (
            "warning_streak",
            lambda history: history.__setitem__("warning_streak", 1),
        ),
        (
            "hash does not match observation",
            lambda history: history["report_files"][0].__setitem__("report_hash", "c" * 64),
        ),
        (
            "positive finite",
            lambda history: history["thresholds"].__setitem__("missing_rate_delta", 0),
        ),
    )
    for message, mutate in mutations:
        mutated = deepcopy(build_research_drift_history(reports))
        mutate(mutated)
        rehash_history(mutated)
        with pytest.raises(DriftHistoryError, match=message):
            verify_research_drift_history(mutated)


@pytest.mark.parametrize(
    ("message", "mutate"),
    (
        ("history_version must be 1", lambda history: history.__setitem__("history_version", 2)),
        ("unsupported scope", lambda history: history.__setitem__("scope", "other")),
        (
            "frozen-model contract",
            lambda history: history.__setitem__("v1_immutable", False),
        ),
        (
            "review_required must be boolean",
            lambda history: history.__setitem__("review_required", "yes"),
        ),
        (
            "minimum_reports must be a positive integer",
            lambda history: history.__setitem__("minimum_reports", 0),
        ),
        (
            "report_count must be at most",
            lambda history: history.__setitem__("report_count", 1_025),
        ),
        (
            "observed_at must include a timezone",
            lambda history: history.__setitem__("observed_at", "2025-04-01T00:00:00"),
        ),
        (
            "baseline fields invalid",
            lambda history: history["baseline"].pop("dataset_id"),
        ),
        (
            "model.artifact_sha256 must be a lowercase SHA-256 digest",
            lambda history: history["model"].__setitem__("artifact_sha256", "invalid"),
        ),
        (
            "positive finite number",
            lambda history: history["thresholds"].__setitem__("missing_rate_delta", -1),
        ),
        (
            "observations must be a list",
            lambda history: history.__setitem__("observations", {}),
        ),
        (
            "report_files must match report_count",
            lambda history: history.__setitem__("report_files", []),
        ),
        (
            "observation 1 must be an object",
            lambda history: history["observations"].__setitem__(0, "invalid"),
        ),
        (
            "observation 1 contains unknown fields",
            lambda history: history["observations"][0].__setitem__("operator_note", "private"),
        ),
        (
            "sequences must be contiguous",
            lambda history: history["observations"][0].__setitem__("sequence", 2),
        ),
        (
            "observation 1 report_hash must be a lowercase SHA-256 digest",
            lambda history: history["observations"][0].__setitem__("report_hash", "A" * 64),
        ),
        (
            "duplicate prospective dataset ID",
            lambda history: history["observations"][1]["prospective"].__setitem__(
                "dataset_id", history["observations"][0]["prospective"]["dataset_id"]
            ),
        ),
        (
            "observations must be chronological",
            lambda history: history["observations"][1]["prospective"].__setitem__(
                "as_of", "2025-01-01"
            ),
        ),
        (
            "event_count must be a non-negative integer",
            lambda history: history["observations"][0]["prospective"].__setitem__(
                "event_count", -1
            ),
        ),
        (
            "included_events cannot exceed event_count",
            lambda history: history["observations"][0]["prospective"].__setitem__(
                "included_events", 13
            ),
        ),
        (
            "observation 1 has an invalid status",
            lambda history: history["observations"][0].__setitem__("status", "unknown"),
        ),
        (
            "feature_warning_count must be a non-negative integer",
            lambda history: history["observations"][0].__setitem__("feature_warning_count", -1),
        ),
        (
            "warning_names must be strings",
            lambda history: history["observations"][0].__setitem__("warning_names", None),
        ),
        (
            "warning_names must be unique",
            lambda history: history["observations"][0].__setitem__("warning_names", ["x", "x"]),
        ),
        (
            "report file 1 must be an object",
            lambda history: history["report_files"].__setitem__(0, "invalid"),
        ),
        (
            "report file 1 contains unknown fields",
            lambda history: history["report_files"][0].__setitem__("operator_note", "private"),
        ),
        (
            "path is invalid",
            lambda history: history["report_files"][0].__setitem__("path", ""),
        ),
        (
            "review_required does not match status",
            lambda history: history.__setitem__("review_required", False),
        ),
        (
            "history_hash must be a lowercase SHA-256 digest",
            lambda history: history.__setitem__("history_hash", "invalid"),
        ),
        (
            "observed_at must be an ISO-8601 timestamp",
            lambda history: history.__setitem__("observed_at", "not-a-timestamp"),
        ),
        (
            "model_id must be a non-empty string",
            lambda history: history["model"].__setitem__("model_id", ""),
        ),
    ),
)
def test_history_rejects_deep_invalid_shapes(
    message: str, mutate: Any
) -> None:
    reports = [
        make_report("prospective-1", "2025-02-01T00:00:00+00:00"),
        make_report("prospective-2", "2025-03-01T00:00:00+00:00"),
    ]
    history = deepcopy(build_research_drift_history(reports))
    mutate(history)
    if message not in {"history_hash must be a lowercase SHA-256 digest"}:
        rehash_history(history)
    with pytest.raises(DriftHistoryError, match=message):
        verify_research_drift_history(history)


def test_history_accepts_date_only_prospective_timestamp() -> None:
    report = make_report("prospective-1", "2025-02-01")

    history = build_research_drift_history([report], minimum_reports=1)

    assert history["status"] == "stable"
    verify_research_drift_history(history)


@pytest.mark.parametrize("status", ("error", "incomplete"))
def test_history_propagates_terminal_report_statuses(status: str) -> None:
    history = build_research_drift_history(
        [make_report("prospective-1", "2025-02-01T00:00:00+00:00", status=status)],
        minimum_reports=1,
    )

    assert history["status"] == status
    assert history["review_required"] is True
    verify_research_drift_history(history)
