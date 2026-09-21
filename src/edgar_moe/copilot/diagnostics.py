"""Safe summaries for operator-supplied forward diagnostic reports.

Forward diagnostics contain forecast-level observations that are useful for
research, but they are too detailed to hand to an LLM by default.  This module
loads an explicitly supplied local report and projects it onto a small,
validated summary.  The projection is deliberately target-aware: it retains
short-horizon counts and metrics while making the official twenty-session
boundary and diagnostic-only disclaimer explicit.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from edgar_moe.utils.timestamps import (
    NaiveTimestampError,
    TimestampFormatError,
    parse_aware_timestamp,
)

MAX_DIAGNOSTIC_BYTES = 4_000_000
DIAGNOSTIC_DISCLAIMER = (
    "Diagnostic-only short-horizon outcomes. They do not replace, modify, or "
    "contribute to the official 20-session forward evaluation."
)

_STATUSES = frozenset({"ready", "awaiting_maturity", "insufficient_coverage"})
_COUNT_FIELDS = (
    "forecast_count",
    "matched_count",
    "matured_count",
    "pending_count",
    "unmatched_count",
)
_METRIC_FIELDS = ("coverage", "rank_ic", "rmse", "mae", "directional_accuracy")
_SUMMARY_FIELDS = (
    "diagnostic",
    "official_horizon_sessions",
    "horizon_sessions",
    "as_of",
    "status",
    *_COUNT_FIELDS,
    *_METRIC_FIELDS,
    "next_maturity_at",
    "latest_maturity_at",
    "disclaimer",
)
_UNIQUE_SUMMARY_FIELDS = (
    "status",
    "official_horizon_sessions",
    "horizon_sessions",
    "as_of",
    "event_count",
    "repeated_forecast_count",
    *_COUNT_FIELDS[:4],
    *_METRIC_FIELDS,
    "next_maturity_at",
    "latest_maturity_at",
)


class DiagnosticSummaryError(ValueError):
    """Raised when a forward diagnostic cannot satisfy the safe summary contract."""


def read_forward_diagnostic_summary(path: Path) -> dict[str, object]:
    """Read and redact one operator-supplied diagnostic report.

    The returned object never contains ``observations``, unmatched forecast
    identifiers, event identifiers, or arbitrary report fields.  A digest of
    the source bytes is retained so a citation can identify the exact private
    artifact without revealing its filesystem path.
    """

    try:
        size = path.stat().st_size
        if size > MAX_DIAGNOSTIC_BYTES:
            raise DiagnosticSummaryError("diagnostic report exceeds the size limit")
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes)
    except DiagnosticSummaryError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise DiagnosticSummaryError("diagnostic report is unavailable") from error
    if not isinstance(raw, Mapping):
        raise DiagnosticSummaryError("diagnostic report must be a JSON object")

    summary = _summary(raw)
    unique = raw.get("unique_event_evaluation")
    if isinstance(unique, Mapping):
        summary["unique_event_evaluation"] = _unique_summary(unique)
    else:
        raise DiagnosticSummaryError("diagnostic report is missing unique-event evaluation")
    summary["source_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    return summary


def _summary(raw: Mapping[str, Any]) -> dict[str, object]:
    if raw.get("diagnostic") is not True:
        raise DiagnosticSummaryError("report is not marked diagnostic-only")
    if raw.get("official_horizon_sessions") != 20:
        raise DiagnosticSummaryError("diagnostic must retain the official 20-session boundary")
    horizon = _bounded_int(raw.get("horizon_sessions"), "horizon_sessions")
    if not 2 <= horizon < 20:
        raise DiagnosticSummaryError("diagnostic horizon must be between 2 and 19 sessions")
    status = raw.get("status")
    if status not in _STATUSES:
        raise DiagnosticSummaryError("diagnostic status is invalid")
    if raw.get("disclaimer") != DIAGNOSTIC_DISCLAIMER:
        raise DiagnosticSummaryError("diagnostic disclaimer is invalid")

    result: dict[str, object] = {
        "diagnostic": True,
        "official_horizon_sessions": 20,
        "horizon_sessions": horizon,
        "as_of": _timestamp(raw.get("as_of"), "as_of"),
        "status": status,
        "disclaimer": DIAGNOSTIC_DISCLAIMER,
    }
    for field in _COUNT_FIELDS:
        result[field] = _bounded_int(raw.get(field), field)
    for field in _METRIC_FIELDS:
        result[field] = _metric(raw.get(field), field)
    result["next_maturity_at"] = _optional_timestamp(raw.get("next_maturity_at"), "next_maturity_at")
    result["latest_maturity_at"] = _optional_timestamp(
        raw.get("latest_maturity_at"), "latest_maturity_at"
    )
    _validate_counts(result)
    return result


def _unique_summary(raw: Mapping[str, Any]) -> dict[str, object]:
    status = raw.get("status")
    if status == "unavailable":
        return {
            "status": "unavailable",
            "reason": "unique-event evaluation is unavailable",
        }
    # The unique report shares the core diagnostic contract, but the source
    # report may contain an extra event/repetition count.
    summary = _summary(raw)
    result: dict[str, object] = {
        key: summary[key]
        for key in _UNIQUE_SUMMARY_FIELDS
        if key in summary
    }
    result["event_count"] = _bounded_int(raw.get("event_count"), "event_count")
    result["repeated_forecast_count"] = _bounded_int(
        raw.get("repeated_forecast_count"), "repeated_forecast_count"
    )
    return result


def _validate_counts(summary: Mapping[str, object]) -> None:
    forecast_count = cast(int, summary["forecast_count"])
    matched_count = cast(int, summary["matched_count"])
    matured_count = cast(int, summary["matured_count"])
    pending_count = cast(int, summary["pending_count"])
    unmatched_count = cast(int, summary["unmatched_count"])
    if matched_count > forecast_count or matured_count + pending_count > matched_count:
        raise DiagnosticSummaryError("diagnostic counts are inconsistent")
    if unmatched_count > forecast_count:
        raise DiagnosticSummaryError("diagnostic unmatched count is inconsistent")


def _bounded_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DiagnosticSummaryError(f"diagnostic {field} must be a non-negative integer")
    if value > 10_000_000:
        raise DiagnosticSummaryError(f"diagnostic {field} exceeds the safety bound")
    return value


def _metric(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiagnosticSummaryError(f"diagnostic {field} must be a finite number or null")
    number = float(value)
    if not math.isfinite(number):
        raise DiagnosticSummaryError(f"diagnostic {field} must be finite")
    return number


def _timestamp(value: object, field: str) -> str:
    try:
        return parse_aware_timestamp(value).isoformat()
    except NaiveTimestampError as error:
        raise DiagnosticSummaryError(f"diagnostic {field} must include a timezone") from error
    except TimestampFormatError as error:
        raise DiagnosticSummaryError(f"diagnostic {field} must be a timestamp") from error


def _optional_timestamp(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _timestamp(value, field)
