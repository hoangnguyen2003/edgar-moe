"""Opt-in, redacted operational alerts for prospective forward runs."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import orjson

from edgar_moe.utils.timestamps import NaiveTimestampError, parse_aware_timestamp

AlertKind = Literal[
    "failed_run",
    "stale_runner",
    "registry_unavailable",
    "quality_failure",
    "quality_warning",
]

_SEVERITY: dict[AlertKind, str] = {
    "failed_run": "critical",
    "registry_unavailable": "critical",
    "stale_runner": "warning",
    "quality_failure": "critical",
    "quality_warning": "warning",
}
_SUMMARY: dict[AlertKind, str] = {
    "failed_run": "Prospective forward cycle failed",
    "stale_runner": "Prospective forward runner is stale",
    "registry_unavailable": "Prospective registry is unavailable",
    "quality_failure": "Prospective quality gate failed",
    "quality_warning": "Prospective quality gate needs review",
}


class AlertDeliveryError(RuntimeError):
    """Raised when a configured webhook cannot be reached successfully."""


def hash_alert_payload(payload: Mapping[str, Any]) -> str:
    """Return a stable SHA-256 identity for a redacted alert payload."""
    return hashlib.sha256(orjson.dumps(dict(payload), option=orjson.OPT_SORT_KEYS)).hexdigest()


def verify_alert_receipt(receipt: Mapping[str, Any]) -> None:
    """Raise when an alert receipt was changed after it was written."""
    expected = str(receipt.get("receipt_hash", ""))
    if len(expected) != 64:
        raise ValueError("Alert receipt is missing a SHA-256 receipt_hash")
    unsigned = dict(receipt)
    unsigned.pop("receipt_hash", None)
    observed = hashlib.sha256(orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)).hexdigest()
    if observed != expected:
        raise ValueError("Alert receipt hash does not match content")


def classify_forward_status(status: Mapping[str, Any]) -> AlertKind | None:
    """Map a registry/status payload to one actionable alert kind.

    The classification is intentionally independent from HTTP status codes so it
    can be used by the scheduled runner and by a read-only API monitor.
    """
    if (
        status.get("available") is False
        or status.get("registry_available") is False
        or status.get("health_status") == "unavailable"
    ):
        return "registry_unavailable"
    if status.get("latest_run_status") == "failed":
        return "failed_run"
    if _positive_int(status.get("latest_quality_failures")):
        return "quality_failure"
    age = _number(status.get("age_seconds"))
    stale_after = _number(status.get("stale_after_seconds"))
    if age is not None and stale_after is not None and age > stale_after:
        return "stale_runner"
    if _positive_int(status.get("latest_quality_warnings")):
        return "quality_warning"
    if status.get("health_status") == "warning":
        return "quality_warning"
    if status.get("health_status") == "degraded":
        # For example, no successful run has ever been recorded, so there is no
        # age to compare with the freshness window.
        return "stale_runner"
    return None


def build_failure_alert(context: Mapping[str, Any]) -> dict[str, Any]:
    """Build a redacted alert payload from the failure-context allowlist."""
    return _build_alert(
        "failed_run",
        observed_at=_timestamp(context.get("observed_at")),
        details={
            "workflow": _text(context.get("workflow")),
            "run_id": _text(context.get("run_id")),
            "run_attempt": _text(context.get("run_attempt")),
            "commit": _text(context.get("commit")),
            "ref": _text(context.get("ref")),
            "cutoff": _text(context.get("cutoff")),
            "device": _text(context.get("device")),
            "cycle_outcome": _text(context.get("cycle_outcome")),
        },
    )


def build_status_alert(
    status: Mapping[str, Any], *, observed_at: datetime | None = None
) -> dict[str, Any] | None:
    """Build an alert for a non-healthy status, or return ``None`` when healthy."""
    kind = classify_forward_status(status)
    if kind is None:
        return None
    details = {
        "health_status": _text(status.get("health_status")),
        "health_message": _text(status.get("health_message")),
        "latest_run_status": _text(status.get("latest_run_status")),
        "latest_successful_run_at": _text(status.get("latest_successful_run_at")),
        "latest_failed_run_at": _text(status.get("latest_failed_run_at")),
        "age_seconds": _number(status.get("age_seconds")),
        "stale_after_seconds": _number(status.get("stale_after_seconds")),
        "latest_quality_warnings": _positive_int(status.get("latest_quality_warnings")) or 0,
        "latest_quality_failures": _positive_int(status.get("latest_quality_failures")) or 0,
    }
    return _build_alert(
        kind,
        observed_at=observed_at or datetime.now(UTC),
        details=details,
    )


def send_webhook(
    url: str,
    payload: Mapping[str, Any],
    *,
    timeout_seconds: float = 10.0,
) -> int:
    """POST a redacted alert to an HTTPS webhook and return its status code."""
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise ValueError("Webhook timeout must be greater than 0 and at most 60 seconds")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Alert webhook URL must be an absolute HTTPS URL")
    request = Request(
        url,
        data=orjson.dumps(payload, option=orjson.OPT_SORT_KEYS),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "edgar-moe-forward-alert/1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise AlertDeliveryError(f"Webhook delivery failed: {type(error).__name__}") from error
    if status < 200 or status >= 300:
        raise AlertDeliveryError(f"Webhook returned HTTP {status}")
    return status


def _build_alert(
    kind: AlertKind,
    *,
    observed_at: datetime,
    details: Mapping[str, Any],
) -> dict[str, Any]:
    observed = _timestamp(observed_at)
    safe_details = {str(key): value for key, value in details.items()}
    identity = {
        "kind": kind,
        "details": safe_details,
    }
    dedupe_key = hashlib.sha256(
        orjson.dumps(identity, option=orjson.OPT_SORT_KEYS | orjson.OPT_UTC_Z)
    ).hexdigest()[:24]
    return {
        "schema_version": 1,
        "source": "edgar-moe",
        "event": "forward_operational_alert",
        "kind": kind,
        "severity": _SEVERITY[kind],
        "summary": _SUMMARY[kind],
        "observed_at": observed.isoformat(),
        "dedupe_key": dedupe_key,
        "details": safe_details,
    }


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Alert timestamps must be timezone-aware")
        return value.astimezone(UTC)
    try:
        return parse_aware_timestamp(str(value))
    except NaiveTimestampError as error:
        raise ValueError("Alert timestamps must be timezone-aware") from error


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _positive_int(value: Any) -> int | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return int(number)
