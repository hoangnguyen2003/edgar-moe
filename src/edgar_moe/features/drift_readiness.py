"""A safe readiness gate for prospective research-drift history."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import orjson

from .drift_history import (
    DriftHistoryError,
    verify_research_drift_history,
)


class DriftReadinessError(ValueError):
    """Raised when a retained drift history cannot form a readiness report."""


_SHA256_LENGTH = 64
_HISTORY_STATUSES = frozenset({"stable", "warning", "error", "incomplete", "insufficient_history"})
_STATUSES = frozenset({"ready", "blocked", "review_required"})
_CHECK_STATUSES = frozenset({"passed", "failed"})
_CHECK_IDS = (
    "history_integrity",
    "minimum_observations",
    "history_contract_minimum",
    "frozen_identity",
    "history_status",
    "review_required",
    "warning_streak",
)
_HARD_BLOCK_STATUSES = frozenset({"insufficient_history", "error", "incomplete"})
_READINESS_KEYS = frozenset(
    {
        "artifact_sha256",
        "automatic_retraining",
        "baseline_dataset_id",
        "baseline_source_manifest_hash",
        "blocked_reasons",
        "checks",
        "disclaimer",
        "frozen_at",
        "history_contract_minimum",
        "history_hash",
        "history_status",
        "model_id",
        "model_version",
        "readiness_sha256",
        "report_count",
        "required_report_count",
        "research_only",
        "scope",
        "schema_version",
        "selection_hash",
        "status",
        "v1_immutable",
        "warning_streak",
    }
)
_DISCLAIMER = (
    "Prospective drift readiness is a structural evidence gate; it does not establish "
    "predictive performance, investment suitability, or permission to retrain."
)


def build_research_drift_readiness(
    history: Mapping[str, Any],
    *,
    minimum_reports: int = 3,
) -> dict[str, Any]:
    """Build a hash-pinned readiness summary without reopening source reports.

    The retained history is the authority for child-report hashes, one frozen
    identity, chronological ordering, and warning status. This gate adds the
    operator decision that enough stable prospective observations exist for a
    human review; it never reads outcomes or authorizes model changes.
    """
    required_reports = _positive_int(minimum_reports, "minimum_reports")
    try:
        verify_research_drift_history(history)
    except DriftHistoryError as error:
        raise DriftReadinessError(str(error)) from error

    history_status = _required_text(history.get("status"), "history status")
    history_contract_minimum = _positive_int(
        history.get("minimum_reports"), "history minimum_reports"
    )
    report_count = _positive_int(history.get("report_count"), "history report_count")
    warning_streak = _nonnegative_int(history.get("warning_streak"), "history warning_streak")
    baseline = _mapping(history.get("baseline"), "history baseline")
    model = _mapping(history.get("model"), "history model")
    frozen_identity = (
        history.get("scope") == "research_drift_history"
        and history.get("v1_immutable") is True
        and history.get("automatic_retraining") is False
    )
    checks = [
        _check("history_integrity", True),
        _check("minimum_observations", report_count >= required_reports),
        _check("history_contract_minimum", report_count >= history_contract_minimum),
        _check("frozen_identity", frozen_identity),
        _check("history_status", history_status == "stable"),
        _check("review_required", history.get("review_required") is False),
        _check(
            "warning_streak",
            warning_streak == 0,
        ),
    ]
    failed_ids = [str(check["check_id"]) for check in checks if check["status"] == "failed"]
    hard_blocked = (
        report_count < required_reports
        or history_status in _HARD_BLOCK_STATUSES
        or "frozen_identity" in failed_ids
    )
    status = "ready" if not failed_ids else "blocked" if hard_blocked else "review_required"
    payload: dict[str, Any] = {
        "schema_version": 1,
        "scope": "research_drift_readiness",
        "research_only": True,
        "v1_immutable": True,
        "automatic_retraining": False,
        "status": status,
        "history_hash": _required_digest(history.get("history_hash"), "history_hash"),
        "history_status": history_status,
        "required_report_count": required_reports,
        "history_contract_minimum": history_contract_minimum,
        "report_count": report_count,
        "warning_streak": warning_streak,
        "baseline_dataset_id": _required_text(baseline.get("dataset_id"), "baseline dataset_id"),
        "baseline_source_manifest_hash": _required_text(
            baseline.get("source_manifest_hash"), "baseline source_manifest_hash"
        ),
        "model_id": _required_text(model.get("model_id"), "model model_id"),
        "model_version": _required_text(model.get("model_version"), "model model_version"),
        "artifact_sha256": _required_digest(model.get("artifact_sha256"), "artifact_sha256"),
        "selection_hash": _required_digest(model.get("selection_hash"), "selection_hash"),
        "frozen_at": _required_text(model.get("frozen_at"), "model frozen_at"),
        "checks": checks,
        "blocked_reasons": failed_ids,
        "disclaimer": _DISCLAIMER,
    }
    payload["readiness_sha256"] = _content_hash(payload)
    verify_research_drift_readiness(payload)
    return payload


def verify_research_drift_readiness(report: Mapping[str, Any]) -> None:
    """Verify a retained readiness summary without reopening its source history."""
    if not isinstance(report, Mapping):
        raise DriftReadinessError("drift readiness must be a JSON object")
    unknown = sorted(str(key) for key in report if key not in _READINESS_KEYS)
    if unknown:
        raise DriftReadinessError("drift readiness contains unknown fields: " + ", ".join(unknown))
    missing = sorted(key for key in _READINESS_KEYS if key not in report)
    if missing:
        raise DriftReadinessError("drift readiness is missing fields: " + ", ".join(missing))
    if report.get("schema_version") != 1:
        raise DriftReadinessError("drift readiness schema_version must be 1")
    if report.get("scope") != "research_drift_readiness":
        raise DriftReadinessError("drift readiness scope is invalid")
    if report.get("research_only") is not True:
        raise DriftReadinessError("drift readiness research_only must be true")
    if report.get("v1_immutable") is not True:
        raise DriftReadinessError("drift readiness v1_immutable must be true")
    if report.get("automatic_retraining") is not False:
        raise DriftReadinessError("drift readiness automatic_retraining must be false")
    if report.get("status") not in _STATUSES:
        raise DriftReadinessError("drift readiness status is invalid")
    if report.get("history_status") not in _HISTORY_STATUSES:
        raise DriftReadinessError("drift readiness history_status is invalid")
    if report.get("disclaimer") != _DISCLAIMER:
        raise DriftReadinessError("drift readiness disclaimer is invalid")
    for field in ("history_hash", "artifact_sha256", "selection_hash"):
        _required_digest(report.get(field), field)
    for field in (
        "baseline_dataset_id",
        "baseline_source_manifest_hash",
        "model_id",
        "model_version",
        "frozen_at",
    ):
        _required_text(report.get(field), field)
    required_reports = _positive_int(report.get("required_report_count"), "required_report_count")
    history_minimum = _positive_int(
        report.get("history_contract_minimum"), "history_contract_minimum"
    )
    report_count = _positive_int(report.get("report_count"), "report_count")
    warning_streak = _nonnegative_int(report.get("warning_streak"), "warning_streak")
    checks = report.get("checks")
    if not isinstance(checks, list) or len(checks) != len(_CHECK_IDS):
        raise DriftReadinessError("drift readiness checks are incomplete")
    seen: set[str] = set()
    failed_ids: list[str] = []
    check_statuses: dict[str, str] = {}
    for check in checks:
        if not isinstance(check, Mapping) or set(check) != {"check_id", "status"}:
            raise DriftReadinessError("drift readiness checks contain unsupported fields")
        check_id = check.get("check_id")
        if check_id not in _CHECK_IDS or check_id in seen:
            raise DriftReadinessError("drift readiness check identifiers are invalid")
        seen.add(str(check_id))
        check_status = check.get("status")
        if check_status not in _CHECK_STATUSES:
            raise DriftReadinessError("drift readiness check status is invalid")
        check_statuses[str(check_id)] = str(check_status)
        if check_status == "failed":
            failed_ids.append(str(check_id))
    if seen != set(_CHECK_IDS):
        raise DriftReadinessError("drift readiness check identifiers are incomplete")
    if check_statuses["history_integrity"] != "passed":
        raise DriftReadinessError("drift readiness history_integrity must pass")
    expected_checks = {
        "minimum_observations": report_count >= required_reports,
        "history_contract_minimum": report_count >= history_minimum,
        "frozen_identity": report.get("v1_immutable") is True
        and report.get("automatic_retraining") is False,
        "history_status": report.get("history_status") == "stable",
        "review_required": report.get("history_status") == "stable",
        "warning_streak": warning_streak == 0,
    }
    for check_id, expected_passed in expected_checks.items():
        expected_status = "passed" if expected_passed else "failed"
        if check_statuses[check_id] != expected_status:
            raise DriftReadinessError(f"drift readiness {check_id} check is inconsistent")
    if report.get("blocked_reasons") != failed_ids:
        raise DriftReadinessError("drift readiness blocked_reasons do not match checks")
    hard_blocked = (
        report_count < required_reports
        or report.get("history_status") in _HARD_BLOCK_STATUSES
        or "frozen_identity" in failed_ids
    )
    expected_status = (
        "ready" if not failed_ids else "blocked" if hard_blocked else "review_required"
    )
    if report.get("status") != expected_status:
        raise DriftReadinessError("drift readiness status is inconsistent with checks")
    expected_hash = _required_digest(report.get("readiness_sha256"), "readiness_sha256")
    unsigned = dict(report)
    unsigned.pop("readiness_sha256", None)
    if _content_hash(unsigned) != expected_hash:
        raise DriftReadinessError("drift readiness content hash mismatch")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DriftReadinessError(f"{label} must be an object")
    return value


def _check(check_id: str, passed: bool) -> dict[str, str]:
    return {"check_id": check_id, "status": "passed" if passed else "failed"}


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DriftReadinessError(f"{label} must be a non-empty string")
    return value


def _required_digest(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise DriftReadinessError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DriftReadinessError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DriftReadinessError(f"{label} must be a non-negative integer")
    return value


def _content_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(dict(value), option=orjson.OPT_SORT_KEYS)).hexdigest()
