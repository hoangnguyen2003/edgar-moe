"""Content-addressed review history for prospective research-drift reports."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from edgar_moe.features.drift import verify_research_drift_report


class DriftHistoryError(ValueError):
    """Raised when drift reports cannot form one immutable review history."""


_MODEL_CONTEXT_KEYS = (
    "model_id",
    "model_version",
    "artifact_sha256",
    "selection_hash",
    "frozen_at",
)
_BASELINE_KEYS = ("dataset_id", "as_of", "source_manifest_hash")


def build_research_drift_history(
    reports: Sequence[Mapping[str, Any]],
    *,
    report_paths: Sequence[str | Path] | None = None,
    minimum_reports: int = 3,
) -> dict[str, Any]:
    """Aggregate target-free drift reports without changing the frozen model.

    Every report must use the same frozen baseline, model identity, and review
    thresholds. Reports are ordered by prospective ``as_of`` and dataset ID;
    duplicate prospective dataset IDs are rejected. This is a review aid, not
    a retraining trigger and it never reads outcomes or labels.
    """
    if minimum_reports < 1:
        raise DriftHistoryError("minimum_reports must be positive")
    if not reports:
        raise DriftHistoryError("at least one drift report is required")
    if report_paths is not None and len(report_paths) != len(reports):
        raise DriftHistoryError("report_paths must match the report count")

    candidates: list[tuple[datetime, str, int, Mapping[str, Any]]] = []
    baseline_identity: dict[str, Any] | None = None
    model_identity: dict[str, str] | None = None
    thresholds: dict[str, Any] | None = None
    for index, report in enumerate(reports):
        try:
            verify_research_drift_report(report)
        except ValueError as error:
            raise DriftHistoryError(f"report {index + 1} failed hash verification") from error
        if report.get("scope") != "research_drift_only":
            raise DriftHistoryError(f"report {index + 1} has an unsupported scope")
        if report.get("v1_immutable") is not True or report.get("automatic_retraining") is not False:
            raise DriftHistoryError(
                f"report {index + 1} does not preserve the frozen-model contract"
            )

        baseline = _mapping(report, "baseline", index)
        prospective = _mapping(report, "prospective", index)
        current_baseline = _identity(baseline, _BASELINE_KEYS, "baseline", index)
        if baseline_identity is None:
            baseline_identity = current_baseline
        elif current_baseline != baseline_identity:
            raise DriftHistoryError("drift reports do not share one baseline identity")

        context = _mapping(report, "context", index)
        current_model = _identity(context, _MODEL_CONTEXT_KEYS, "model context", index)
        if model_identity is None:
            model_identity = current_model
        elif current_model != model_identity:
            raise DriftHistoryError("drift reports do not share one frozen model identity")

        current_thresholds = _mapping(report, "thresholds", index)
        if thresholds is None:
            thresholds = dict(current_thresholds)
        elif dict(current_thresholds) != thresholds:
            raise DriftHistoryError("drift reports use different review thresholds")

        dataset_id = _string(prospective, "dataset_id", "prospective", index)
        as_of = _parse_timestamp(prospective, "as_of", "prospective", index)
        candidates.append((as_of, dataset_id, index, report))

    candidates.sort(key=lambda item: (item[0], item[1]))
    dataset_ids = [item[1] for item in candidates]
    if len(set(dataset_ids)) != len(dataset_ids):
        raise DriftHistoryError("drift history contains a duplicate prospective dataset ID")

    observations: list[dict[str, Any]] = []
    report_files: list[dict[str, Any]] = []
    statuses: list[str] = []
    for position, (_, dataset_id, original_index, report) in enumerate(candidates):
        status = str(report.get("status", ""))
        if status not in {"stable", "warning", "error", "incomplete"}:
            raise DriftHistoryError(f"report {original_index + 1} has an unknown status")
        summary = _mapping(report, "summary", original_index)
        prospective = _mapping(report, "prospective", original_index)
        report_hash = _string(report, "report_hash", "drift report", original_index)
        observations.append(
            {
                "sequence": position + 1,
                "report_hash": report_hash,
                "prospective": {
                    "dataset_id": dataset_id,
                    "as_of": _string(prospective, "as_of", "prospective", original_index),
                    "event_count": prospective.get("event_count"),
                    "included_events": prospective.get("included_events"),
                },
                "status": status,
                "feature_warning_count": int(summary.get("feature_warning_count", 0)),
                "component_warning_count": int(summary.get("component_warning_count", 0)),
                "warning_names": [str(name) for name in summary.get("warning_names", [])],
            }
        )
        report_path = None if report_paths is None else str(report_paths[original_index])
        report_files.append({"path": report_path, "report_hash": report_hash})
        statuses.append(status)

    status = _history_status(statuses, len(observations), minimum_reports)
    payload: dict[str, Any] = {
        "history_version": 1,
        "scope": "research_drift_history",
        "v1_immutable": True,
        "automatic_retraining": False,
        "status": status,
        "review_required": status != "stable",
        "minimum_reports": minimum_reports,
        "report_count": len(observations),
        "observed_at": datetime.now(UTC).isoformat(),
        "baseline": baseline_identity,
        "model": model_identity,
        "thresholds": thresholds,
        "warning_streak": _warning_streak(statuses),
        "observations": observations,
        "report_files": report_files,
    }
    payload["history_hash"] = _content_hash(payload)
    return payload


def verify_research_drift_history(history: Mapping[str, Any]) -> None:
    """Verify the history content hash and its basic immutable contract."""
    expected = str(history.get("history_hash", ""))
    if len(expected) != 64:
        raise DriftHistoryError("drift history is missing a SHA-256 history_hash")
    unsigned = dict(history)
    unsigned.pop("history_hash", None)
    if _content_hash(unsigned) != expected:
        raise DriftHistoryError("drift history content hash mismatch")
    if history.get("scope") != "research_drift_history":
        raise DriftHistoryError("drift history has an unsupported scope")
    if history.get("v1_immutable") is not True or history.get("automatic_retraining") is not False:
        raise DriftHistoryError("drift history violates the frozen-model contract")


def _history_status(statuses: Sequence[str], count: int, minimum_reports: int) -> str:
    if "error" in statuses:
        return "error"
    if "incomplete" in statuses:
        return "incomplete"
    if "warning" in statuses:
        return "warning"
    if count < minimum_reports:
        return "insufficient_history"
    return "stable"


def _warning_streak(statuses: Sequence[str]) -> int:
    streak = 0
    for status in reversed(statuses):
        if status != "warning":
            break
        streak += 1
    return streak


def _mapping(payload: Mapping[str, Any], key: str, index: int) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise DriftHistoryError(f"report {index + 1} has no mapping for {key}")
    return value


def _identity(
    payload: Mapping[str, Any], keys: Sequence[str], label: str, index: int
) -> dict[str, Any]:
    return {key: _string(payload, key, label, index) for key in keys}


def _string(payload: Mapping[str, Any], key: str, label: str, index: int) -> str:
    value = payload.get(key)
    if value is None or not str(value):
        raise DriftHistoryError(f"report {index + 1} has no {label} field {key}")
    return str(value)


def _parse_timestamp(
    payload: Mapping[str, Any], key: str, label: str, index: int
) -> datetime:
    value = _string(payload, key, label, index)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DriftHistoryError(f"report {index + 1} has an invalid {label} timestamp") from error
    if parsed.tzinfo is None:
        if len(value) == 10 and "T" not in value:
            return parsed.replace(tzinfo=UTC)
        raise DriftHistoryError(f"report {index + 1} has a timezone-naive {label} timestamp")
    return parsed


def _content_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        orjson.dumps(dict(payload), option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
