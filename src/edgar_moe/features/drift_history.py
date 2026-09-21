"""Content-addressed review history for prospective research-drift reports."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Collection, Mapping, Sequence
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
_HISTORY_KEYS = {
    "automatic_retraining",
    "baseline",
    "history_hash",
    "history_version",
    "minimum_reports",
    "model",
    "observations",
    "observed_at",
    "report_count",
    "report_files",
    "review_required",
    "scope",
    "status",
    "thresholds",
    "v1_immutable",
    "warning_streak",
}
_OBSERVATION_KEYS = {
    "component_warning_count",
    "feature_warning_count",
    "prospective",
    "report_hash",
    "sequence",
    "status",
    "warning_names",
}
_PROSPECTIVE_KEYS = {"as_of", "dataset_id", "event_count", "included_events"}
_REPORT_FILE_KEYS = {"path", "report_hash"}
_REPORT_STATUSES = frozenset({"stable", "warning", "error", "incomplete"})
_HISTORY_STATUSES = _REPORT_STATUSES | {"insufficient_history"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_HISTORY_REPORTS = 1_024


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
        if (
            report.get("v1_immutable") is not True
            or report.get("automatic_retraining") is not False
        ):
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
    verify_research_drift_history(payload)
    return payload


def verify_research_drift_history(history: Mapping[str, Any]) -> None:
    """Verify the history hash and every retained aggregate invariant."""
    unknown = _unknown_keys(history, _HISTORY_KEYS)
    if unknown:
        raise DriftHistoryError("drift history contains unknown fields: " + ", ".join(unknown))
    if history.get("history_version") != 1:
        raise DriftHistoryError("drift history history_version must be 1")
    if history.get("scope") != "research_drift_history":
        raise DriftHistoryError("drift history has an unsupported scope")
    if history.get("v1_immutable") is not True or history.get("automatic_retraining") is not False:
        raise DriftHistoryError("drift history violates the frozen-model contract")

    status = history.get("status")
    if status not in _HISTORY_STATUSES:
        raise DriftHistoryError("drift history has an invalid status")
    review_required = history.get("review_required")
    if not isinstance(review_required, bool):
        raise DriftHistoryError("drift history review_required must be boolean")
    minimum_reports = _positive_int(history.get("minimum_reports"), "minimum_reports")
    report_count = _positive_int(history.get("report_count"), "report_count")
    if report_count > _MAX_HISTORY_REPORTS:
        raise DriftHistoryError(
            f"drift history report_count must be at most {_MAX_HISTORY_REPORTS}"
        )
    _parse_history_timestamp(history.get("observed_at"), "observed_at")

    baseline = _exact_mapping(history.get("baseline"), _BASELINE_KEYS, "baseline")
    _validate_identity(baseline, _BASELINE_KEYS, "baseline")
    _parse_history_timestamp(baseline.get("as_of"), "baseline.as_of", allow_date_only=True)
    model = _exact_mapping(history.get("model"), _MODEL_CONTEXT_KEYS, "model")
    _validate_model_identity(model)
    thresholds = _exact_mapping(
        history.get("thresholds"),
        ("missing_rate_delta", "standardized_mean_shift", "population_stability_index"),
        "thresholds",
    )
    for name, value in thresholds.items():
        _positive_finite(value, f"thresholds.{name}")

    observations_value = history.get("observations")
    if not isinstance(observations_value, list):
        raise DriftHistoryError("drift history observations must be a list")
    if len(observations_value) != report_count:
        raise DriftHistoryError("drift history report_count does not match observations")
    report_files_value = history.get("report_files")
    if not isinstance(report_files_value, list) or len(report_files_value) != report_count:
        raise DriftHistoryError("drift history report_files must match report_count")

    statuses: list[str] = []
    seen_dataset_ids: set[str] = set()
    previous_order: tuple[datetime, str] | None = None
    for index, raw_observation in enumerate(observations_value, start=1):
        if not isinstance(raw_observation, Mapping):
            raise DriftHistoryError(f"drift history observation {index} must be an object")
        unknown_observation = _unknown_keys(raw_observation, _OBSERVATION_KEYS)
        if unknown_observation:
            raise DriftHistoryError(
                f"drift history observation {index} contains unknown fields: "
                + ", ".join(unknown_observation)
            )
        if raw_observation.get("sequence") != index:
            raise DriftHistoryError("drift history observation sequences must be contiguous")
        report_hash = raw_observation.get("report_hash")
        _sha256(report_hash, f"observation {index} report_hash")
        prospective = _exact_mapping(
            raw_observation.get("prospective"),
            _PROSPECTIVE_KEYS,
            f"observation {index} prospective",
        )
        dataset_id = _non_empty_string(
            prospective.get("dataset_id"), f"observation {index} prospective.dataset_id"
        )
        if dataset_id in seen_dataset_ids:
            raise DriftHistoryError("drift history contains a duplicate prospective dataset ID")
        seen_dataset_ids.add(dataset_id)
        as_of = _parse_history_timestamp(
            prospective.get("as_of"),
            f"observation {index} prospective.as_of",
            allow_date_only=True,
        )
        current_order = (as_of, dataset_id)
        if previous_order is not None and current_order < previous_order:
            raise DriftHistoryError("drift history observations must be chronological")
        previous_order = current_order
        event_count = _non_negative_int(
            prospective.get("event_count"), f"observation {index} prospective.event_count"
        )
        included_events = _non_negative_int(
            prospective.get("included_events"), f"observation {index} prospective.included_events"
        )
        if included_events > event_count:
            raise DriftHistoryError(
                f"observation {index} included_events cannot exceed event_count"
            )
        observation_status = raw_observation.get("status")
        if observation_status not in _REPORT_STATUSES:
            raise DriftHistoryError(f"observation {index} has an invalid status")
        statuses.append(str(observation_status))
        _non_negative_int(
            raw_observation.get("feature_warning_count"),
            f"observation {index} feature_warning_count",
        )
        _non_negative_int(
            raw_observation.get("component_warning_count"),
            f"observation {index} component_warning_count",
        )
        warning_names = raw_observation.get("warning_names")
        if not isinstance(warning_names, list) or not all(
            isinstance(name, str) and name.strip() for name in warning_names
        ):
            raise DriftHistoryError(f"observation {index} warning_names must be strings")
        if len(set(warning_names)) != len(warning_names):
            raise DriftHistoryError(f"observation {index} warning_names must be unique")

        report_file = report_files_value[index - 1]
        if not isinstance(report_file, Mapping):
            raise DriftHistoryError(f"drift history report file {index} must be an object")
        unknown_file = _unknown_keys(report_file, _REPORT_FILE_KEYS)
        if unknown_file:
            raise DriftHistoryError(
                f"drift history report file {index} contains unknown fields: "
                + ", ".join(unknown_file)
            )
        path = report_file.get("path")
        if path is not None and (not isinstance(path, str) or not path.strip()):
            raise DriftHistoryError(f"drift history report file {index} path is invalid")
        if (
            _sha256(report_file.get("report_hash"), f"report file {index} report_hash")
            != report_hash
        ):
            raise DriftHistoryError(
                f"drift history report file {index} hash does not match observation"
            )

    expected_status = _history_status(statuses, report_count, minimum_reports)
    if status != expected_status:
        raise DriftHistoryError("drift history status does not match observations")
    if review_required != (status != "stable"):
        raise DriftHistoryError("drift history review_required does not match status")
    expected_streak = _warning_streak(statuses)
    if history.get("warning_streak") != expected_streak:
        raise DriftHistoryError("drift history warning_streak does not match observations")

    expected = _sha256(history.get("history_hash"), "history_hash")
    unsigned = dict(history)
    unsigned.pop("history_hash", None)
    if _content_hash(unsigned) != expected:
        raise DriftHistoryError("drift history content hash mismatch")


def _unknown_keys(payload: Mapping[str, Any], allowed: set[str]) -> list[str]:
    return sorted(str(key) for key in payload if key not in allowed)


def _exact_mapping(value: object, keys: Collection[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DriftHistoryError(f"drift history {label} must be an object")
    expected = set(keys)
    unknown = _unknown_keys(value, expected)
    missing = sorted(key for key in expected if key not in value)
    if unknown or missing:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise DriftHistoryError(f"drift history {label} fields invalid: {'; '.join(details)}")
    return value


def _validate_identity(payload: Mapping[str, Any], keys: Sequence[str], label: str) -> None:
    for key in keys:
        _non_empty_string(payload.get(key), f"{label}.{key}")


def _validate_model_identity(payload: Mapping[str, Any]) -> None:
    _validate_identity(payload, _MODEL_CONTEXT_KEYS, "model")
    _sha256(payload.get("artifact_sha256"), "model.artifact_sha256")
    _sha256(payload.get("selection_hash"), "model.selection_hash")
    _parse_history_timestamp(payload.get("frozen_at"), "model.frozen_at")


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise DriftHistoryError(f"drift history {label} must be a lowercase SHA-256 digest")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DriftHistoryError(f"drift history {label} must be a positive integer")
    return value


def _non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DriftHistoryError(f"drift history {label} must be a non-negative integer")
    return value


def _positive_finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DriftHistoryError(f"drift history {label} must be a positive finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise DriftHistoryError(f"drift history {label} must be a positive finite number")
    return numeric


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DriftHistoryError(f"drift history {label} must be a non-empty string")
    return value


def _parse_history_timestamp(
    value: object, label: str, *, allow_date_only: bool = False
) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DriftHistoryError(f"drift history {label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DriftHistoryError(f"drift history {label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None and allow_date_only and len(value) == 10 and "T" not in value:
        return parsed.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        raise DriftHistoryError(f"drift history {label} must include a timezone")
    return parsed.astimezone(UTC)


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


def _parse_timestamp(payload: Mapping[str, Any], key: str, label: str, index: int) -> datetime:
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
    return hashlib.sha256(orjson.dumps(dict(payload), option=orjson.OPT_SORT_KEYS)).hexdigest()
