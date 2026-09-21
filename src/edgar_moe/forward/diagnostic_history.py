"""Content-addressed history for redacted short-horizon diagnostics.

The official forward registry remains the authority for the frozen model's
twenty-session evaluation.  This module only retains a safe, chronological
summary of operator-supplied short-horizon diagnostic artifacts so repeated
engineering observations can be reviewed without mixing them into that
official ledger.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson

from edgar_moe.copilot.diagnostics import DIAGNOSTIC_DISCLAIMER, read_forward_diagnostic_summary
from edgar_moe.utils.timestamps import parse_aware_timestamp

HISTORY_VERSION = 1
OFFICIAL_HORIZON_SESSIONS = 20
MAX_HISTORY_REPORTS = 128
MAX_HISTORY_BYTES = 2_000_000

_HISTORY_STATUSES = frozenset({"insufficient_history", "review_required", "ready"})
_REPORT_STATUSES = frozenset({"ready", "awaiting_maturity", "insufficient_coverage"})
_COUNT_FIELDS = (
    "forecast_count",
    "matched_count",
    "matured_count",
    "pending_count",
    "unmatched_count",
)
_METRIC_FIELDS = ("coverage", "rank_ic", "rmse", "mae", "directional_accuracy")
_SUMMARY_FIELDS = frozenset(
    {
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
        "unique_event_evaluation",
        "source_sha256",
    }
)
_OBSERVATION_KEYS = frozenset(
    {
        "sequence",
        "source_sha256",
        "as_of",
        "status",
        "horizon_sessions",
        *_COUNT_FIELDS,
        *_METRIC_FIELDS,
        "next_maturity_at",
        "latest_maturity_at",
        "unique_event_evaluation",
    }
)
_UNIQUE_KEYS = frozenset(
    {
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
    }
)
_HISTORY_KEYS = frozenset(
    {
        "history_version",
        "scope",
        "research_only",
        "v1_immutable",
        "automatic_retraining",
        "official_evaluation_untouched",
        "status",
        "review_required",
        "minimum_reports",
        "report_count",
        "official_horizon_sessions",
        "horizon_sessions",
        "latest_as_of",
        "disclaimer",
        "observations",
        "history_sha256",
    }
)
_DISCLAIMER = (
    "Forward diagnostic history is research-only short-horizon evidence; it does not replace, "
    "modify, or contribute to the official 20-session forward evaluation."
)
_SHA256 = frozenset("0123456789abcdef")


class DiagnosticHistoryError(ValueError):
    """Raised when a diagnostic history is malformed or unsafe."""


def read_forward_diagnostic_history(path: Path) -> dict[str, Any]:
    """Read and verify one retained history without reopening source reports."""
    try:
        if path.stat().st_size > MAX_HISTORY_BYTES:
            raise DiagnosticHistoryError("diagnostic history exceeds the size limit")
        payload = orjson.loads(path.read_bytes())
    except DiagnosticHistoryError:
        raise
    except (OSError, orjson.JSONDecodeError) as error:
        raise DiagnosticHistoryError("diagnostic history is unavailable") from error
    if not isinstance(payload, dict):
        raise DiagnosticHistoryError("diagnostic history must be an object")
    verify_forward_diagnostic_history(payload)
    return payload


def load_forward_diagnostic_reports(paths: Sequence[Path]) -> tuple[dict[str, object], ...]:
    """Load operator files through the strict diagnostic redaction boundary."""
    if not paths:
        raise DiagnosticHistoryError("at least one diagnostic report is required")
    if len(paths) > MAX_HISTORY_REPORTS:
        raise DiagnosticHistoryError(
            f"at most {MAX_HISTORY_REPORTS} diagnostic reports may be combined"
        )
    try:
        return tuple(read_forward_diagnostic_summary(path) for path in paths)
    except ValueError as error:
        raise DiagnosticHistoryError(str(error)) from error


def build_forward_diagnostic_history(
    reports: Sequence[Mapping[str, Any]],
    *,
    minimum_reports: int = 3,
) -> dict[str, Any]:
    """Build a chronological, redacted history from validated summaries.

    ``reports`` must already be the output of
    :func:`read_forward_diagnostic_summary`.  Requiring the source digest and
    rejecting unknown fields prevents callers from accidentally passing a raw
    diagnostic containing observations.
    """
    minimum = _positive_int(minimum_reports, "minimum_reports")
    if minimum > MAX_HISTORY_REPORTS:
        raise DiagnosticHistoryError(f"minimum_reports cannot exceed {MAX_HISTORY_REPORTS}")
    if len(reports) == 0:
        raise DiagnosticHistoryError("at least one diagnostic report is required")
    if len(reports) > MAX_HISTORY_REPORTS:
        raise DiagnosticHistoryError(
            f"at most {MAX_HISTORY_REPORTS} diagnostic reports may be combined"
        )

    normalized = [_normalize_summary(report, index) for index, report in enumerate(reports)]
    normalized.sort(key=lambda item: (item["as_of"], item["source_sha256"]))
    normalized = [{**item, "sequence": index} for index, item in enumerate(normalized, start=1)]
    _validate_sequence(normalized)
    horizon = int(normalized[0]["horizon_sessions"])
    statuses = [str(item["status"]) for item in normalized]
    report_count = len(normalized)
    if report_count < minimum:
        status = "insufficient_history"
    elif all(item == "ready" for item in statuses):
        status = "ready"
    else:
        status = "review_required"

    payload: dict[str, Any] = {
        "history_version": HISTORY_VERSION,
        "scope": "forward_diagnostic_history",
        "research_only": True,
        "v1_immutable": True,
        "automatic_retraining": False,
        "official_evaluation_untouched": True,
        "status": status,
        "review_required": status != "ready",
        "minimum_reports": minimum,
        "report_count": report_count,
        "official_horizon_sessions": OFFICIAL_HORIZON_SESSIONS,
        "horizon_sessions": horizon,
        "latest_as_of": normalized[-1]["as_of"],
        "disclaimer": _DISCLAIMER,
        "observations": normalized,
    }
    payload["history_sha256"] = _content_hash(payload)
    verify_forward_diagnostic_history(payload)
    return payload


def verify_forward_diagnostic_history(history: Mapping[str, Any]) -> None:
    """Verify the history contract without reopening any source report."""
    if not isinstance(history, Mapping):
        raise DiagnosticHistoryError("diagnostic history must be an object")
    unknown = sorted(str(key) for key in history if key not in _HISTORY_KEYS)
    if unknown:
        raise DiagnosticHistoryError(
            "diagnostic history contains unknown fields: " + ", ".join(unknown)
        )
    required = _HISTORY_KEYS
    missing = sorted(key for key in required if key not in history)
    if missing:
        raise DiagnosticHistoryError("diagnostic history is missing fields: " + ", ".join(missing))
    if history.get("history_version") != HISTORY_VERSION:
        raise DiagnosticHistoryError("diagnostic history version is unsupported")
    if history.get("scope") != "forward_diagnostic_history":
        raise DiagnosticHistoryError("diagnostic history scope is invalid")
    if history.get("research_only") is not True:
        raise DiagnosticHistoryError("diagnostic history must be research-only")
    if history.get("v1_immutable") is not True:
        raise DiagnosticHistoryError("diagnostic history must preserve v1 immutability")
    if history.get("automatic_retraining") is not False:
        raise DiagnosticHistoryError("diagnostic history cannot authorize retraining")
    if history.get("official_evaluation_untouched") is not True:
        raise DiagnosticHistoryError("diagnostic history must not alter official evaluation")
    status = history.get("status")
    if status not in _HISTORY_STATUSES:
        raise DiagnosticHistoryError("diagnostic history status is invalid")
    if not isinstance(history.get("review_required"), bool):
        raise DiagnosticHistoryError("diagnostic history review_required must be boolean")
    minimum = _positive_int(history.get("minimum_reports"), "minimum_reports")
    report_count = _positive_int(history.get("report_count"), "report_count")
    if report_count > MAX_HISTORY_REPORTS:
        raise DiagnosticHistoryError("diagnostic history report_count exceeds the safety bound")
    if history.get("official_horizon_sessions") != OFFICIAL_HORIZON_SESSIONS:
        raise DiagnosticHistoryError("diagnostic history official horizon is invalid")
    horizon = _bounded_int(history.get("horizon_sessions"), "horizon_sessions")
    if not 2 <= horizon < OFFICIAL_HORIZON_SESSIONS:
        raise DiagnosticHistoryError("diagnostic history horizon is invalid")
    latest_as_of = _parse_timestamp(history.get("latest_as_of"), "latest_as_of")
    if history.get("disclaimer") != _DISCLAIMER:
        raise DiagnosticHistoryError("diagnostic history disclaimer is invalid")

    raw_observations = history.get("observations")
    if not isinstance(raw_observations, list) or len(raw_observations) != report_count:
        raise DiagnosticHistoryError("diagnostic history observations do not match report_count")
    observations: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_observations, start=1):
        observations.append(_verify_observation(raw, index, horizon))
    if observations[-1]["as_of"] != latest_as_of.isoformat():
        raise DiagnosticHistoryError("diagnostic history latest_as_of is inconsistent")
    _validate_sequence(observations)
    statuses = [str(item["status"]) for item in observations]
    expected_status = (
        "insufficient_history"
        if report_count < minimum
        else "ready"
        if all(item == "ready" for item in statuses)
        else "review_required"
    )
    if status != expected_status:
        raise DiagnosticHistoryError("diagnostic history status is inconsistent")
    if history.get("review_required") is not (status != "ready"):
        raise DiagnosticHistoryError("diagnostic history review_required is inconsistent")
    expected_hash = history.get("history_sha256")
    if not _is_sha256(expected_hash):
        raise DiagnosticHistoryError("diagnostic history is missing a SHA-256 history_sha256")
    unsigned = dict(history)
    unsigned.pop("history_sha256", None)
    if _content_hash(unsigned) != expected_hash:
        raise DiagnosticHistoryError("diagnostic history content hash mismatch")


def _normalize_summary(report: Mapping[str, Any], index: int) -> dict[str, Any]:
    unknown = sorted(str(key) for key in report if key not in _SUMMARY_FIELDS)
    if unknown:
        raise DiagnosticHistoryError(
            f"diagnostic report {index + 1} contains unsafe fields: {', '.join(unknown)}"
        )
    missing = sorted(key for key in _SUMMARY_FIELDS if key not in report)
    if missing:
        raise DiagnosticHistoryError(
            f"diagnostic report {index + 1} is missing fields: {', '.join(missing)}"
        )
    if report.get("diagnostic") is not True:
        raise DiagnosticHistoryError(f"diagnostic report {index + 1} is not diagnostic-only")
    if report.get("official_horizon_sessions") != OFFICIAL_HORIZON_SESSIONS:
        raise DiagnosticHistoryError(
            f"diagnostic report {index + 1} has an invalid official horizon"
        )
    horizon = _bounded_int(report.get("horizon_sessions"), f"report {index + 1} horizon_sessions")
    if not 2 <= horizon < OFFICIAL_HORIZON_SESSIONS:
        raise DiagnosticHistoryError(f"diagnostic report {index + 1} horizon is invalid")
    status = report.get("status")
    if status not in _REPORT_STATUSES:
        raise DiagnosticHistoryError(f"diagnostic report {index + 1} status is invalid")
    if report.get("disclaimer") != DIAGNOSTIC_DISCLAIMER:
        raise DiagnosticHistoryError(f"diagnostic report {index + 1} disclaimer is invalid")
    source_sha256 = report.get("source_sha256")
    if not _is_sha256(source_sha256):
        raise DiagnosticHistoryError(f"diagnostic report {index + 1} source_sha256 is invalid")
    result: dict[str, Any] = {
        "source_sha256": source_sha256,
        "as_of": _parse_timestamp(report.get("as_of"), f"report {index + 1} as_of").isoformat(),
        "status": status,
        "horizon_sessions": horizon,
    }
    for field in _COUNT_FIELDS:
        result[field] = _bounded_int(report.get(field), f"report {index + 1} {field}")
    for field in _METRIC_FIELDS:
        result[field] = _metric(report.get(field), f"report {index + 1} {field}")
    result["next_maturity_at"] = _optional_timestamp(
        report.get("next_maturity_at"), f"report {index + 1} next_maturity_at"
    )
    result["latest_maturity_at"] = _optional_timestamp(
        report.get("latest_maturity_at"), f"report {index + 1} latest_maturity_at"
    )
    unique = _normalize_unique(report.get("unique_event_evaluation"), index)
    if unique.get("status") != "unavailable":
        if unique["official_horizon_sessions"] != OFFICIAL_HORIZON_SESSIONS:
            raise DiagnosticHistoryError(
                f"diagnostic report {index + 1} unique official horizon is inconsistent"
            )
        if unique["horizon_sessions"] != horizon:
            raise DiagnosticHistoryError(
                f"diagnostic report {index + 1} unique horizon does not match report horizon"
            )
        if unique["as_of"] != result["as_of"]:
            raise DiagnosticHistoryError(
                f"diagnostic report {index + 1} unique as_of does not match report as_of"
            )
        _validate_unique_counts(unique, f"diagnostic report {index + 1}")
    result["unique_event_evaluation"] = unique
    _validate_counts(result, f"diagnostic report {index + 1}")
    return result


def _normalize_unique(value: object, index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DiagnosticHistoryError(f"diagnostic report {index + 1} unique evaluation is invalid")
    if value.get("status") == "unavailable":
        return {"status": "unavailable", "reason": "unique-event evaluation is unavailable"}
    # The loader already removes raw selected IDs and observations.  Recheck
    # this boundary here so callers cannot bypass it by constructing a mapping.
    unknown = sorted(str(key) for key in value if key not in _UNIQUE_KEYS)
    if unknown:
        raise DiagnosticHistoryError(
            f"diagnostic report {index + 1} unique evaluation contains unsafe fields: "
            + ", ".join(unknown)
        )
    missing = sorted(key for key in _UNIQUE_KEYS if key not in value)
    if missing:
        raise DiagnosticHistoryError(
            f"diagnostic report {index + 1} unique evaluation is missing fields: "
            + ", ".join(missing)
        )
    status = value.get("status")
    if status not in _REPORT_STATUSES:
        raise DiagnosticHistoryError(f"diagnostic report {index + 1} unique status is invalid")
    if value.get("official_horizon_sessions") != OFFICIAL_HORIZON_SESSIONS:
        raise DiagnosticHistoryError(
            f"diagnostic report {index + 1} unique official horizon is invalid"
        )
    horizon = _bounded_int(value.get("horizon_sessions"), "unique horizon_sessions")
    if not 2 <= horizon < OFFICIAL_HORIZON_SESSIONS:
        raise DiagnosticHistoryError(f"diagnostic report {index + 1} unique horizon is invalid")
    result: dict[str, Any] = {
        "status": status,
        "official_horizon_sessions": OFFICIAL_HORIZON_SESSIONS,
        "horizon_sessions": horizon,
        "as_of": _parse_timestamp(value.get("as_of"), "unique as_of").isoformat(),
        "event_count": _bounded_int(value.get("event_count"), "unique event_count"),
        "repeated_forecast_count": _bounded_int(
            value.get("repeated_forecast_count"), "unique repeated_forecast_count"
        ),
    }
    for field in _COUNT_FIELDS[:4]:
        result[field] = _bounded_int(value.get(field), f"unique {field}")
    for field in _METRIC_FIELDS:
        result[field] = _metric(value.get(field), f"unique {field}")
    result["next_maturity_at"] = _optional_timestamp(
        value.get("next_maturity_at"), "unique next_maturity_at"
    )
    result["latest_maturity_at"] = _optional_timestamp(
        value.get("latest_maturity_at"), "unique latest_maturity_at"
    )
    return result


def _verify_observation(value: object, index: int, horizon: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DiagnosticHistoryError(f"diagnostic history observation {index} is invalid")
    unknown = sorted(str(key) for key in value if key not in _OBSERVATION_KEYS)
    if unknown:
        raise DiagnosticHistoryError(
            f"diagnostic history observation {index} contains unknown fields: " + ", ".join(unknown)
        )
    missing = sorted(key for key in _OBSERVATION_KEYS if key not in value)
    if missing:
        raise DiagnosticHistoryError(
            f"diagnostic history observation {index} is missing fields: " + ", ".join(missing)
        )
    if value.get("sequence") != index:
        raise DiagnosticHistoryError("diagnostic history observation sequences must be contiguous")
    if value.get("horizon_sessions") != horizon:
        raise DiagnosticHistoryError("diagnostic history reports use mixed horizons")
    if not _is_sha256(value.get("source_sha256")):
        raise DiagnosticHistoryError(
            f"diagnostic history observation {index} source_sha256 is invalid"
        )
    status = value.get("status")
    if status not in _REPORT_STATUSES:
        raise DiagnosticHistoryError(f"diagnostic history observation {index} status is invalid")
    result: dict[str, Any] = {
        "sequence": index,
        "source_sha256": value["source_sha256"],
        "as_of": _parse_timestamp(value.get("as_of"), f"observation {index} as_of").isoformat(),
        "status": status,
        "horizon_sessions": horizon,
    }
    for field in _COUNT_FIELDS:
        result[field] = _bounded_int(value.get(field), f"observation {index} {field}")
    for field in _METRIC_FIELDS:
        result[field] = _metric(value.get(field), f"observation {index} {field}")
    result["next_maturity_at"] = _optional_timestamp(
        value.get("next_maturity_at"), f"observation {index} next_maturity_at"
    )
    result["latest_maturity_at"] = _optional_timestamp(
        value.get("latest_maturity_at"), f"observation {index} latest_maturity_at"
    )
    unique = _verify_unique(value.get("unique_event_evaluation"), index)
    if unique.get("status") != "unavailable":
        if unique["horizon_sessions"] != horizon:
            raise DiagnosticHistoryError(
                f"diagnostic history observation {index} unique horizon does not match"
            )
        if unique["as_of"] != result["as_of"]:
            raise DiagnosticHistoryError(
                f"diagnostic history observation {index} unique as_of does not match"
            )
        _validate_unique_counts(unique, f"diagnostic history observation {index}")
    result["unique_event_evaluation"] = unique
    _validate_counts(result, f"diagnostic history observation {index}")
    return result


def _verify_unique(value: object, index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DiagnosticHistoryError(
            f"diagnostic history observation {index} unique evaluation is invalid"
        )
    if value.get("status") == "unavailable":
        if set(value) != {"status", "reason"}:
            raise DiagnosticHistoryError(
                f"diagnostic history observation {index} unavailable unique evaluation is invalid"
            )
        return {"status": "unavailable", "reason": "unique-event evaluation is unavailable"}
    return _normalize_unique(value, index)


def _validate_sequence(observations: Sequence[Mapping[str, Any]]) -> None:
    seen_sources: set[str] = set()
    seen_as_of: set[str] = set()
    previous: tuple[datetime, str] | None = None
    for index, observation in enumerate(observations, start=1):
        if observation.get("sequence") not in {None, index}:
            raise DiagnosticHistoryError("diagnostic history observation sequences are invalid")
        source = str(observation["source_sha256"])
        if source in seen_sources:
            raise DiagnosticHistoryError("diagnostic history contains a duplicate source artifact")
        seen_sources.add(source)
        as_of_text = str(observation["as_of"])
        if as_of_text in seen_as_of:
            raise DiagnosticHistoryError(
                "diagnostic history contains duplicate observation timestamps"
            )
        seen_as_of.add(as_of_text)
        parsed = _parse_timestamp(as_of_text, "observation as_of")
        current = (parsed, source)
        if previous is not None and current < previous:
            raise DiagnosticHistoryError("diagnostic history observations must be chronological")
        previous = current


def _validate_counts(summary: Mapping[str, Any], label: str) -> None:
    forecast_count = int(summary["forecast_count"])
    matched_count = int(summary["matched_count"])
    matured_count = int(summary["matured_count"])
    pending_count = int(summary["pending_count"])
    unmatched_count = int(summary["unmatched_count"])
    if matched_count > forecast_count or matured_count + pending_count > matched_count:
        raise DiagnosticHistoryError(f"{label} counts are inconsistent")
    if unmatched_count > forecast_count:
        raise DiagnosticHistoryError(f"{label} unmatched count is inconsistent")


def _validate_unique_counts(summary: Mapping[str, Any], label: str) -> None:
    forecast_count = int(summary["forecast_count"])
    matched_count = int(summary["matched_count"])
    matured_count = int(summary["matured_count"])
    pending_count = int(summary["pending_count"])
    if matched_count > forecast_count or matured_count + pending_count > matched_count:
        raise DiagnosticHistoryError(f"{label} unique counts are inconsistent")


def _bounded_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10_000_000:
        raise DiagnosticHistoryError(f"{label} must be a bounded non-negative integer")
    return value


def _positive_int(value: object, label: str) -> int:
    result = _bounded_int(value, label)
    if result < 1:
        raise DiagnosticHistoryError(f"{label} must be positive")
    return result


def _metric(value: object, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiagnosticHistoryError(f"{label} must be finite or null")
    result = float(value)
    if not math.isfinite(result):
        raise DiagnosticHistoryError(f"{label} must be finite")
    return result


def _parse_timestamp(value: object, label: str) -> datetime:
    try:
        return parse_aware_timestamp(value)
    except ValueError as error:
        raise DiagnosticHistoryError(f"{label} {error}") from error


def _optional_timestamp(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _parse_timestamp(value, label).isoformat()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _SHA256


def _content_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(dict(payload), option=orjson.OPT_SORT_KEYS)).hexdigest()
