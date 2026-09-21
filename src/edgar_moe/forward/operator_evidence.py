"""Validation for redacted, operator-supplied production evidence packets."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson

_ALL_CHECK_IDS = (
    "database_least_privilege",
    "restore_rehearsal",
    "partial_write_reconciliation",
    "deployment_smoke",
    "alert_delivery",
    "capacity_baseline",
    "drift_history",
)
_CHECK_IDS = frozenset(_ALL_CHECK_IDS)
REQUIRED_PROVIDER_CHECKS = (
    "database_least_privilege",
    "restore_rehearsal",
    "partial_write_reconciliation",
)
P1_PROVIDER_CHECKS = (
    "deployment_smoke",
    "alert_delivery",
    "capacity_baseline",
    "drift_history",
)
PROVIDER_EVIDENCE_PROFILES = {
    "p0": REQUIRED_PROVIDER_CHECKS,
    "p1": REQUIRED_PROVIDER_CHECKS + P1_PROVIDER_CHECKS,
    "full": _ALL_CHECK_IDS,
}
_STATUSES = frozenset({"passed", "failed", "not_run", "not_applicable"})
_PACKET_KEYS = frozenset(
    {
        "schema_version",
        "packet_id",
        "captured_at",
        "operator",
        "provider",
        "target",
        "scope",
        "checks",
        "artifacts",
        "redaction",
        "packet_sha256",
    }
)
_CHECK_KEYS = frozenset({"check_id", "status", "observed_at", "evidence_refs", "notes"})
_ARTIFACT_KEYS = frozenset({"name", "sha256", "size_bytes", "retention_days"})
_REDACTION_KEYS = frozenset({"secrets_excluded", "provider_urls_excluded", "raw_payloads_excluded"})
_PACKET_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELATIVE_ARTIFACT = re.compile(r"^[^/][^\r\n]*$")
_URL = re.compile(r"(?i)\b(?:https?|postgres(?:ql)?|mysql|redis|s3|r2)://")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?key|secret(?:[_-]?key)?|token|password|"
    r"private[_-]?key|database[_-]?url|connection[_-]?string)\b\s*[:=]\s*\S+"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----")
_SENSITIVE_KEY = re.compile(
    r"(?i)^(?:api[_-]?key|access[_-]?key(?:[_-]?id)?|secret(?:[_-]?key)?|token|"
    r"password|private[_-]?key|database[_-]?url|connection[_-]?string)$"
)


class OperatorEvidenceError(ValueError):
    """Raised when a redacted operator evidence packet is malformed."""


class OperatorReadinessError(ValueError):
    """Raised when a retained operator readiness report is malformed."""


_READINESS_STATUSES = frozenset({"ready", "blocked", "stale"})
_READINESS_SCOPE = "operator_evidence_readiness"
_READINESS_DISCLAIMER = (
    "Operator evidence readiness is a time-bounded verification of retained metadata; "
    "it does not create, upgrade, or independently prove provider-side observations."
)
_READINESS_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "scope",
        "status",
        "packet_id",
        "packet_sha256",
        "captured_at",
        "evaluated_at",
        "profile",
        "max_age_seconds",
        "required_check_ids",
        "required_checks",
        "blocked_checks",
        "stale_checks",
        "disclaimer",
        "readiness_sha256",
    }
)


def prepare_operator_evidence_packet(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a packet draft and add its canonical content hash."""
    _validate_packet(payload, require_hash=False)
    packet = dict(payload)
    supplied_hash = packet.get("packet_sha256")
    packet.pop("packet_sha256", None)
    computed_hash = _content_hash(packet)
    if supplied_hash is not None and supplied_hash != computed_hash:
        raise OperatorEvidenceError("packet_sha256 does not match packet content")
    packet["packet_sha256"] = computed_hash
    return packet


def verify_operator_evidence_packet(payload: Mapping[str, Any]) -> None:
    """Verify packet structure, redaction guarantees, and its content hash."""
    _validate_packet(payload, require_hash=True)
    expected = str(payload["packet_sha256"])
    unsigned = dict(payload)
    unsigned.pop("packet_sha256", None)
    if _content_hash(unsigned) != expected:
        raise OperatorEvidenceError("packet_sha256 does not match packet content")


def packet_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a non-sensitive summary suitable for a CI log or step summary."""
    verify_operator_evidence_packet(payload)
    checks = payload["checks"]
    assert isinstance(checks, list)
    counts = {status: 0 for status in sorted(_STATUSES)}
    for check in checks:
        assert isinstance(check, Mapping)
        status = str(check["status"])
        counts[status] += 1
    return {
        "status": "verified",
        "packet_id": payload["packet_id"],
        "packet_sha256": payload["packet_sha256"],
        "check_counts": counts,
        "artifact_count": len(payload["artifacts"]),
    }


def operator_readiness(
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(days=30),
    profile: str = "p0",
) -> dict[str, Any]:
    """Summarize whether provider controls in a readiness profile are current.

    This is intentionally a read-only decision aid. It verifies the packet but
    never upgrades ``not_run`` or ``failed`` checks and never treats a packet
    as evidence that was not referenced by the packet itself.
    """
    if max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    if not isinstance(profile, str) or profile not in PROVIDER_EVIDENCE_PROFILES:
        raise ValueError("profile must be one of: " + ", ".join(PROVIDER_EVIDENCE_PROFILES))
    verify_operator_evidence_packet(payload)
    observed_now = now or datetime.now(UTC)
    if observed_now.tzinfo is None or observed_now.utcoffset() is None:
        raise ValueError("now must include a UTC offset")
    observed_now = observed_now.astimezone(UTC)

    checks = payload["checks"]
    assert isinstance(checks, list)
    by_id = {str(check["check_id"]): check for check in checks if isinstance(check, Mapping)}
    results: list[dict[str, Any]] = []
    blocked: list[str] = []
    stale: list[str] = []
    required_check_ids = PROVIDER_EVIDENCE_PROFILES[profile]
    for check_id in required_check_ids:
        check = by_id.get(check_id)
        if check is None:
            results.append({"check_id": check_id, "status": "missing", "fresh": False})
            blocked.append(check_id)
            continue
        status = str(check["status"])
        observed_at = _parse_timestamp(check["observed_at"], f"{check_id}.observed_at")
        age = observed_now - observed_at
        age_seconds = int(age.total_seconds())
        fresh = timedelta(0) <= age <= max_age
        result = {
            "check_id": check_id,
            "status": status,
            "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
            "age_seconds": age_seconds,
            "fresh": fresh,
            "evidence_ref_count": len(check["evidence_refs"]),
        }
        results.append(result)
        if status != "passed" or age_seconds < 0:
            blocked.append(check_id)
        elif not fresh:
            stale.append(check_id)

    readiness_status = "blocked" if blocked else "stale" if stale else "ready"
    return {
        "status": readiness_status,
        "packet_id": payload["packet_id"],
        "packet_sha256": payload["packet_sha256"],
        "captured_at": payload["captured_at"],
        "profile": profile,
        "max_age_seconds": int(max_age.total_seconds()),
        "required_check_ids": list(required_check_ids),
        "required_checks": results,
        "blocked_checks": blocked,
        "stale_checks": stale,
    }


def build_operator_readiness_report(
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(days=30),
    profile: str = "p0",
) -> dict[str, Any]:
    """Build an immutable, hash-pinned readiness decision for a packet.

    The report retains only packet identity, profile metadata, and derived check
    results. It deliberately does not copy packet contents or provider evidence.
    """
    if max_age.total_seconds() != int(max_age.total_seconds()):
        raise ValueError("max_age must resolve to a whole number of seconds")
    evaluated_at = now or datetime.now(UTC)
    summary = operator_readiness(
        payload,
        now=evaluated_at,
        max_age=max_age,
        profile=profile,
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": _READINESS_SCOPE,
        "status": summary["status"],
        "packet_id": summary["packet_id"],
        "packet_sha256": summary["packet_sha256"],
        "captured_at": summary["captured_at"],
        "evaluated_at": _format_utc(evaluated_at),
        "profile": summary["profile"],
        "max_age_seconds": summary["max_age_seconds"],
        "required_check_ids": summary["required_check_ids"],
        "required_checks": summary["required_checks"],
        "blocked_checks": summary["blocked_checks"],
        "stale_checks": summary["stale_checks"],
        "disclaimer": _READINESS_DISCLAIMER,
    }
    report["readiness_sha256"] = _content_hash(report)
    verify_operator_readiness_report(report)
    return report


def verify_operator_readiness_report(report: Mapping[str, Any]) -> None:
    """Verify a retained readiness report without reopening its packet."""
    if not isinstance(report, Mapping):
        raise OperatorReadinessError("operator readiness report must be a JSON object")
    unknown = sorted(str(key) for key in report if key not in _READINESS_REPORT_KEYS)
    if unknown:
        raise OperatorReadinessError(
            "operator readiness report contains unsupported fields: " + ", ".join(unknown)
        )
    missing = sorted(key for key in _READINESS_REPORT_KEYS if key not in report)
    if missing:
        raise OperatorReadinessError(
            "operator readiness report is missing fields: " + ", ".join(missing)
        )
    if report.get("schema_version") != 1 or report.get("scope") != _READINESS_SCOPE:
        raise OperatorReadinessError("operator readiness report schema or scope is invalid")
    if report.get("status") not in _READINESS_STATUSES:
        raise OperatorReadinessError("operator readiness report status is invalid")
    if report.get("disclaimer") != _READINESS_DISCLAIMER:
        raise OperatorReadinessError("operator readiness report disclaimer is invalid")
    _readiness_identifier(report.get("packet_id"), "packet_id")
    for field in ("packet_sha256", "readiness_sha256"):
        _readiness_digest(report.get(field), field)
    captured_at = _readiness_timestamp(report.get("captured_at"), "captured_at")
    evaluated_at = _readiness_timestamp(report.get("evaluated_at"), "evaluated_at")
    if evaluated_at < captured_at:
        raise OperatorReadinessError("evaluated_at must not precede captured_at")

    profile = report.get("profile")
    if not isinstance(profile, str) or profile not in PROVIDER_EVIDENCE_PROFILES:
        raise OperatorReadinessError("operator readiness report profile is invalid")
    required_ids = report.get("required_check_ids")
    expected_ids = list(PROVIDER_EVIDENCE_PROFILES[profile])
    if required_ids != expected_ids:
        raise OperatorReadinessError("required_check_ids do not match the selected profile")
    max_age_seconds = report.get("max_age_seconds")
    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, int)
        or max_age_seconds <= 0
    ):
        raise OperatorReadinessError("max_age_seconds must be a positive integer")

    results = report.get("required_checks")
    if not isinstance(results, list) or len(results) != len(expected_ids):
        raise OperatorReadinessError("required_checks do not cover the selected profile")
    blocked: list[str] = []
    stale: list[str] = []
    for expected_id, result in zip(expected_ids, results, strict=True):
        if not isinstance(result, Mapping) or result.get("check_id") != expected_id:
            raise OperatorReadinessError(
                "required_checks are out of order or contain an invalid id"
            )
        status = result.get("status")
        if status == "missing":
            if set(result) != {"check_id", "status", "fresh"} or result.get("fresh") is not False:
                raise OperatorReadinessError("missing readiness checks have invalid fields")
            blocked.append(expected_id)
            continue
        if status not in _STATUSES:
            raise OperatorReadinessError("required check status is invalid")
        if set(result) != {
            "check_id",
            "status",
            "observed_at",
            "age_seconds",
            "fresh",
            "evidence_ref_count",
        }:
            raise OperatorReadinessError("required check result fields are invalid")
        observed_at = _readiness_timestamp(result.get("observed_at"), f"{expected_id}.observed_at")
        age_seconds = result.get("age_seconds")
        if isinstance(age_seconds, bool) or not isinstance(age_seconds, int):
            raise OperatorReadinessError(f"{expected_id}.age_seconds must be an integer")
        expected_age = int((evaluated_at - observed_at).total_seconds())
        if age_seconds != expected_age:
            raise OperatorReadinessError(f"{expected_id}.age_seconds is inconsistent")
        fresh = result.get("fresh")
        expected_fresh = 0 <= age_seconds <= max_age_seconds
        if fresh is not expected_fresh:
            raise OperatorReadinessError(f"{expected_id}.fresh is inconsistent")
        evidence_ref_count = result.get("evidence_ref_count")
        if (
            isinstance(evidence_ref_count, bool)
            or not isinstance(evidence_ref_count, int)
            or evidence_ref_count < 0
        ):
            raise OperatorReadinessError(f"{expected_id}.evidence_ref_count is invalid")
        if status != "passed" or age_seconds < 0:
            blocked.append(expected_id)
        elif not fresh:
            stale.append(expected_id)

    if report.get("blocked_checks") != blocked:
        raise OperatorReadinessError("blocked_checks are inconsistent with required_checks")
    if report.get("stale_checks") != stale:
        raise OperatorReadinessError("stale_checks are inconsistent with required_checks")
    expected_status = "blocked" if blocked else "stale" if stale else "ready"
    if report.get("status") != expected_status:
        raise OperatorReadinessError("operator readiness report status is inconsistent")

    unsigned = dict(report)
    unsigned.pop("readiness_sha256", None)
    if _content_hash(unsigned) != str(report["readiness_sha256"]):
        raise OperatorReadinessError("operator readiness report content hash mismatch")


def _validate_packet(payload: Mapping[str, Any], *, require_hash: bool) -> None:
    if not isinstance(payload, Mapping):
        raise OperatorEvidenceError("packet must be a JSON object")
    unknown = set(payload) - _PACKET_KEYS
    if unknown:
        raise OperatorEvidenceError(f"packet contains unsupported fields: {sorted(unknown)}")
    required = _PACKET_KEYS - {"packet_sha256"}
    missing = required - set(payload)
    if missing:
        raise OperatorEvidenceError(f"packet is missing fields: {sorted(missing)}")
    if require_hash and "packet_sha256" not in payload:
        raise OperatorEvidenceError("packet is missing packet_sha256")
    if "packet_sha256" in payload and not _SHA256.fullmatch(str(payload["packet_sha256"])):
        raise OperatorEvidenceError("packet_sha256 must be a lowercase SHA-256 digest")

    if payload["schema_version"] != 1:
        raise OperatorEvidenceError("unsupported operator evidence schema_version")
    _require_identifier(payload["packet_id"], "packet_id")
    _require_timestamp(payload["captured_at"], "captured_at")
    for field in ("operator", "provider", "target"):
        _require_text(payload[field], field, maximum=160)

    scope = _require_string_list(payload["scope"], "scope")
    if not scope:
        raise OperatorEvidenceError("scope must contain at least one check")
    if len(set(scope)) != len(scope):
        raise OperatorEvidenceError("scope contains duplicate check identifiers")
    unknown_scope = set(scope) - _CHECK_IDS
    if unknown_scope:
        raise OperatorEvidenceError(f"scope contains unsupported checks: {sorted(unknown_scope)}")

    artifacts = _validate_artifacts(payload["artifacts"])
    checks = _validate_checks(payload["checks"], set(scope), set(artifacts))
    if set(checks) != set(scope):
        raise OperatorEvidenceError("checks must exactly cover scope")
    _validate_redaction(payload["redaction"])
    _reject_sensitive_values(payload)


def _validate_checks(value: Any, scope: set[str], artifacts: set[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        raise OperatorEvidenceError("checks must be a non-empty list")
    identifiers: list[str] = []
    for check in value:
        if not isinstance(check, Mapping):
            raise OperatorEvidenceError("each check must be an object")
        unknown = set(check) - _CHECK_KEYS
        if unknown:
            raise OperatorEvidenceError(f"check contains unsupported fields: {sorted(unknown)}")
        for field in ("check_id", "status", "observed_at", "evidence_refs"):
            if field not in check:
                raise OperatorEvidenceError(f"check is missing {field}")
        identifier = str(check["check_id"])
        if identifier not in _CHECK_IDS:
            raise OperatorEvidenceError(f"unsupported check identifier: {identifier}")
        if identifier in identifiers:
            raise OperatorEvidenceError(f"duplicate check identifier: {identifier}")
        identifiers.append(identifier)
        status = str(check["status"])
        if status not in _STATUSES:
            raise OperatorEvidenceError(f"unsupported check status: {status}")
        _require_timestamp(check["observed_at"], f"{identifier}.observed_at")
        refs = _require_string_list(check["evidence_refs"], f"{identifier}.evidence_refs")
        if len(set(refs)) != len(refs):
            raise OperatorEvidenceError(f"{identifier}.evidence_refs contains duplicates")
        unknown_refs = set(refs) - artifacts
        if unknown_refs:
            raise OperatorEvidenceError(
                f"{identifier}.evidence_refs references unknown artifacts: {sorted(unknown_refs)}"
            )
        notes = check.get("notes")
        if notes is not None:
            _require_text(notes, f"{identifier}.notes", maximum=2_000)
        if status in {"passed", "failed"} and not refs:
            raise OperatorEvidenceError(f"{identifier} requires evidence_refs for status {status}")
        if status in {"not_run", "not_applicable"} and not str(notes or "").strip():
            raise OperatorEvidenceError(f"{identifier} requires notes for status {status}")
    if set(identifiers) != scope:
        raise OperatorEvidenceError("checks must exactly cover scope")
    return identifiers


def _validate_artifacts(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise OperatorEvidenceError("artifacts must be a list")
    names: list[str] = []
    for artifact in value:
        if not isinstance(artifact, Mapping):
            raise OperatorEvidenceError("each artifact must be an object")
        unknown = set(artifact) - _ARTIFACT_KEYS
        if unknown:
            raise OperatorEvidenceError(f"artifact contains unsupported fields: {sorted(unknown)}")
        for field in ("name", "sha256", "size_bytes"):
            if field not in artifact:
                raise OperatorEvidenceError(f"artifact is missing {field}")
        name = str(artifact["name"])
        if (
            not _RELATIVE_ARTIFACT.fullmatch(name)
            or name in {".", ".."}
            or "\\" in name
            or ":" in name
        ):
            raise OperatorEvidenceError(f"artifact name must be a relative file name: {name}")
        if "/../" in f"/{name}/" or name.endswith("/.."):
            raise OperatorEvidenceError(
                f"artifact name must not escape its evidence directory: {name}"
            )
        if name in names:
            raise OperatorEvidenceError(f"duplicate artifact name: {name}")
        names.append(name)
        if not _SHA256.fullmatch(str(artifact["sha256"])):
            raise OperatorEvidenceError(f"artifact {name} has an invalid SHA-256 digest")
        _require_nonnegative_int(artifact["size_bytes"], f"{name}.size_bytes")
        if "retention_days" in artifact:
            _require_nonnegative_int(artifact["retention_days"], f"{name}.retention_days")
    return names


def _validate_redaction(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise OperatorEvidenceError("redaction must be an object")
    unknown = set(value) - _REDACTION_KEYS
    if unknown:
        raise OperatorEvidenceError(f"redaction contains unsupported fields: {sorted(unknown)}")
    for key in _REDACTION_KEYS:
        if value.get(key) is not True:
            raise OperatorEvidenceError(f"redaction.{key} must be true")


def _reject_sensitive_values(value: Any, path: str = "packet") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY.fullmatch(key_text):
                raise OperatorEvidenceError(f"sensitive field is not allowed: {path}.{key_text}")
            _reject_sensitive_values(child, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_values(child, f"{path}[{index}]")
    elif isinstance(value, str) and (
        _URL.search(value) or _SECRET_ASSIGNMENT.search(value) or _PRIVATE_KEY.search(value)
    ):
        raise OperatorEvidenceError(f"sensitive value is not allowed at {path}")


def _require_identifier(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _PACKET_ID.fullmatch(value):
        raise OperatorEvidenceError(f"{field} must match {_PACKET_ID.pattern}")


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise OperatorEvidenceError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise OperatorEvidenceError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise OperatorEvidenceError(f"{field} must include a UTC offset")
    return parsed.astimezone(UTC)


def _require_timestamp(value: Any, field: str) -> None:
    _parse_timestamp(value, field)


def _require_text(value: Any, field: str, *, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise OperatorEvidenceError(
            f"{field} must be a non-empty string of at most {maximum} chars"
        )


def _require_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise OperatorEvidenceError(f"{field} must be a list of non-empty strings")
    return [str(item) for item in value]


def _require_nonnegative_int(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OperatorEvidenceError(f"{field} must be a non-negative integer")


def _readiness_identifier(value: object, field: str) -> None:
    if not isinstance(value, str) or not _PACKET_ID.fullmatch(value):
        raise OperatorReadinessError(f"{field} must match {_PACKET_ID.pattern}")


def _readiness_digest(value: object, field: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise OperatorReadinessError(f"{field} must be a lowercase SHA-256 digest")


def _readiness_timestamp(value: object, field: str) -> datetime:
    try:
        return _parse_timestamp(value, field)
    except OperatorEvidenceError as error:
        raise OperatorReadinessError(str(error)) from error


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _content_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(dict(payload), option=orjson.OPT_SORT_KEYS)).hexdigest()
