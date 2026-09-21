"""Cross-domain readiness decisions for the EDGAR-MoE platform.

The individual readiness gates are deliberately owned by their respective
subsystems.  This module only composes their already-verified, redacted
summaries into one operator decision.  It never opens a source report, reads
provider payloads, or turns an incomplete control into a pass.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import orjson

PLATFORM_SCOPE = "platform_readiness"
PLATFORM_DISCLAIMER = (
    "Platform readiness is a cross-domain evidence decision; it does not prove "
    "provider operations, establish predictive performance, authorize retraining, "
    "publish data, or provide investment advice."
)

CONTROL_IDS = (
    "public_release",
    "operator_evidence",
    "research_copilot",
    "prospective_drift",
)
CONTROL_SCOPES = {
    "public_release": "public_release_readiness",
    "operator_evidence": "operator_evidence_readiness",
    "research_copilot": "research_copilot_readiness",
    "prospective_drift": "research_drift_readiness",
}
_CONTROL_STATUSES = frozenset({"ready", "review_required", "blocked", "stale", "missing"})
_READINESS_STATUSES = frozenset({"ready", "review_required", "blocked"})
_REPORT_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REPORT_KEYS = frozenset(
    {
        "scope",
        "status",
        "report_sha256",
        "blocked_reasons",
        "review_reasons",
    }
)
_CONTROL_KEYS = frozenset(
    {
        "control_id",
        "scope",
        "status",
        "report_sha256",
        "blocked_reasons",
        "review_reasons",
    }
)
_PLATFORM_KEYS = frozenset(
    {
        "schema_version",
        "scope",
        "research_only",
        "v1_immutable",
        "automatic_retraining",
        "status",
        "controls",
        "blocked_reasons",
        "review_reasons",
        "disclaimer",
        "evaluated_at",
        "readiness_sha256",
    }
)


class PlatformReadinessError(ValueError):
    """Raised when a platform readiness decision is malformed or unsafe."""


def build_platform_readiness(
    reports: Mapping[str, Mapping[str, Any] | None],
    *,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    """Compose verified subsystem summaries into a hash-pinned decision.

    ``reports`` must contain one entry for each control in ``CONTROL_IDS``.  A
    ``None`` value is an explicit missing control and therefore blocks the
    platform decision.  Source-specific schema verification belongs to the
    callers that load the individual reports; this function validates the
    redacted cross-domain fields it consumes and never trusts an undeclared
    control.
    """

    if set(reports) != set(CONTROL_IDS):
        missing = sorted(set(CONTROL_IDS) - set(reports))
        unknown = sorted(set(reports) - set(CONTROL_IDS))
        details: list[str] = []
        if missing:
            details.append("missing controls: " + ", ".join(missing))
        if unknown:
            details.append("unknown controls: " + ", ".join(unknown))
        raise PlatformReadinessError(
            "platform readiness controls are invalid (" + "; ".join(details) + ")"
        )

    controls: list[dict[str, Any]] = []
    blocked_reasons: list[str] = []
    review_reasons: list[str] = []
    for control_id in CONTROL_IDS:
        source_report = reports[control_id]
        control = _control_summary(control_id, source_report)
        controls.append(control)
        status = str(control["status"])
        if status in {"missing", "blocked", "stale"}:
            blocked_reasons.append(f"{control_id}:{status}")
        elif status == "review_required":
            review_reasons.append(f"{control_id}:review_required")
    controls_by_id = {str(control["control_id"]): control for control in controls}
    blocked_reasons.extend(
        f"{control_id}:{reason}"
        for control_id in CONTROL_IDS
        for reason in controls_by_id[control_id]["blocked_reasons"]
    )
    review_reasons.extend(
        f"{control_id}:{reason}"
        for control_id in CONTROL_IDS
        for reason in controls_by_id[control_id]["review_reasons"]
    )

    status = "blocked" if blocked_reasons else "review_required" if review_reasons else "ready"
    observed_at = evaluated_at or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise PlatformReadinessError("evaluated_at must include a UTC offset")
    observed_at = observed_at.astimezone(UTC)
    platform_report: dict[str, Any] = {
        "schema_version": 1,
        "scope": PLATFORM_SCOPE,
        "research_only": True,
        "v1_immutable": True,
        "automatic_retraining": False,
        "status": status,
        "controls": controls,
        "blocked_reasons": blocked_reasons,
        "review_reasons": review_reasons,
        "disclaimer": PLATFORM_DISCLAIMER,
        "evaluated_at": _format_utc(observed_at),
    }
    platform_report["readiness_sha256"] = _content_hash(platform_report)
    verify_platform_readiness(platform_report)
    return platform_report


def verify_platform_readiness(report: Mapping[str, Any]) -> None:
    """Verify a retained platform decision without reopening source reports."""

    if not isinstance(report, Mapping):
        raise PlatformReadinessError("platform readiness must be a JSON object")
    unknown = sorted(str(key) for key in report if key not in _PLATFORM_KEYS)
    missing = sorted(key for key in _PLATFORM_KEYS if key not in report)
    if unknown:
        raise PlatformReadinessError(
            "platform readiness contains unknown fields: " + ", ".join(unknown)
        )
    if missing:
        raise PlatformReadinessError("platform readiness is missing fields: " + ", ".join(missing))
    if report.get("schema_version") != 1 or report.get("scope") != PLATFORM_SCOPE:
        raise PlatformReadinessError("platform readiness schema or scope is invalid")
    if report.get("research_only") is not True:
        raise PlatformReadinessError("platform readiness research_only must be true")
    if report.get("v1_immutable") is not True:
        raise PlatformReadinessError("platform readiness v1_immutable must be true")
    if report.get("automatic_retraining") is not False:
        raise PlatformReadinessError("platform readiness automatic_retraining must be false")
    if report.get("status") not in _READINESS_STATUSES:
        raise PlatformReadinessError("platform readiness status is invalid")
    if report.get("disclaimer") != PLATFORM_DISCLAIMER:
        raise PlatformReadinessError("platform readiness disclaimer is invalid")
    _timestamp(report.get("evaluated_at"))
    controls = report.get("controls")
    if not isinstance(controls, list) or len(controls) != len(CONTROL_IDS):
        raise PlatformReadinessError("platform readiness controls are incomplete")
    observed: dict[str, Mapping[str, Any]] = {}
    for item in controls:
        if not isinstance(item, Mapping):
            raise PlatformReadinessError("platform readiness control must be an object")
        if set(item) != _CONTROL_KEYS:
            raise PlatformReadinessError("platform readiness control fields are invalid")
        control_id = item.get("control_id")
        if control_id not in CONTROL_SCOPES or control_id in observed:
            raise PlatformReadinessError("platform readiness control identifiers are invalid")
        if item.get("scope") != CONTROL_SCOPES[control_id]:
            raise PlatformReadinessError(f"platform readiness scope is invalid for {control_id}")
        status = item.get("status")
        if status not in _CONTROL_STATUSES:
            raise PlatformReadinessError(f"platform readiness status is invalid for {control_id}")
        _verify_reasons(item.get("blocked_reasons"), f"{control_id}.blocked_reasons")
        _verify_reasons(item.get("review_reasons"), f"{control_id}.review_reasons")
        digest = item.get("report_sha256")
        if digest is not None and (
            not isinstance(digest, str) or not _REPORT_DIGEST.fullmatch(digest)
        ):
            raise PlatformReadinessError(
                f"platform readiness report digest is invalid for {control_id}"
            )
        if status == "missing" and digest is not None:
            raise PlatformReadinessError(
                f"missing control {control_id} must not have a report digest"
            )
        if status != "missing" and digest is None:
            raise PlatformReadinessError(f"present control {control_id} must have a report digest")
        if status in {"blocked", "stale", "missing"} and not item["blocked_reasons"]:
            raise PlatformReadinessError(f"blocked control {control_id} must include a reason")
        if status == "review_required" and not item["review_reasons"]:
            raise PlatformReadinessError(
                f"review-required control {control_id} must include a reason"
            )
        if status == "ready" and (item["blocked_reasons"] or item["review_reasons"]):
            raise PlatformReadinessError(f"ready control {control_id} cannot include reasons")
        observed[str(control_id)] = item
    if set(observed) != set(CONTROL_IDS):
        raise PlatformReadinessError("platform readiness control identifiers are incomplete")

    blocked = [
        f"{control_id}:{status}"
        for control_id in CONTROL_IDS
        if (status := str(observed[control_id]["status"])) in {"missing", "blocked", "stale"}
    ]
    blocked.extend(
        f"{control_id}:{reason}"
        for control_id in CONTROL_IDS
        for reason in observed[control_id]["blocked_reasons"]
    )
    reviews = [
        f"{control_id}:review_required"
        for control_id in CONTROL_IDS
        if observed[control_id]["status"] == "review_required"
    ]
    reviews.extend(
        f"{control_id}:{reason}"
        for control_id in CONTROL_IDS
        for reason in observed[control_id]["review_reasons"]
    )
    if report.get("blocked_reasons") != blocked:
        raise PlatformReadinessError("platform readiness blocked_reasons do not match controls")
    if report.get("review_reasons") != reviews:
        raise PlatformReadinessError("platform readiness review_reasons do not match controls")
    expected_status = "blocked" if blocked else "review_required" if reviews else "ready"
    if report.get("status") != expected_status:
        raise PlatformReadinessError("platform readiness status is inconsistent with controls")
    digest = report.get("readiness_sha256")
    if not isinstance(digest, str) or not _REPORT_DIGEST.fullmatch(digest):
        raise PlatformReadinessError("platform readiness hash is invalid")
    unsigned = dict(report)
    unsigned.pop("readiness_sha256", None)
    if _content_hash(unsigned) != digest:
        raise PlatformReadinessError("platform readiness content hash mismatch")


def _control_summary(control_id: str, report: Mapping[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return {
            "control_id": control_id,
            "scope": CONTROL_SCOPES[control_id],
            "status": "missing",
            "report_sha256": None,
            "blocked_reasons": ["report_missing"],
            "review_reasons": [],
        }
    if not isinstance(report, Mapping):
        raise PlatformReadinessError(f"{control_id} report must be an object")
    unknown = sorted(str(key) for key in report if key not in _REPORT_KEYS)
    if unknown:
        raise PlatformReadinessError(
            f"{control_id} report contains unsupported fields: " + ", ".join(unknown)
        )
    if report.get("scope") != CONTROL_SCOPES[control_id]:
        raise PlatformReadinessError(f"{control_id} report scope is invalid")
    status = report.get("status")
    if status not in _CONTROL_STATUSES - {"missing"}:
        raise PlatformReadinessError(f"{control_id} report status is invalid")
    digest = report.get("report_sha256")
    if not isinstance(digest, str) or not _REPORT_DIGEST.fullmatch(digest):
        raise PlatformReadinessError(f"{control_id} report digest is invalid")
    blocked = _reason_list(report.get("blocked_reasons"), f"{control_id}.blocked_reasons")
    reviews = _reason_list(report.get("review_reasons"), f"{control_id}.review_reasons")
    if status in {"blocked", "stale"} and not blocked:
        blocked = ["source_report_blocked"] if status == "blocked" else ["source_report_stale"]
    if status == "review_required" and not reviews:
        reviews = ["source_report_review_required"]
    if status == "ready" and (blocked or reviews):
        raise PlatformReadinessError(f"ready {control_id} report cannot include reasons")
    return {
        "control_id": control_id,
        "scope": CONTROL_SCOPES[control_id],
        "status": status,
        "report_sha256": digest,
        "blocked_reasons": blocked,
        "review_reasons": reviews,
    }


def _verify_reasons(value: object, label: str) -> None:
    _reason_list(value, label)


def _reason_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and re.fullmatch(r"[a-z0-9_.:-]{1,160}", item) for item in value
    ):
        raise PlatformReadinessError(f"{label} must be a list of safe reason codes")
    return list(value)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise PlatformReadinessError("platform readiness evaluated_at must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PlatformReadinessError("platform readiness evaluated_at is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.astimezone(UTC) != parsed:
        raise PlatformReadinessError("platform readiness evaluated_at must include a UTC offset")
    return parsed


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _content_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS)).hexdigest()
