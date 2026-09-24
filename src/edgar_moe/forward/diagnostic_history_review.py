"""Self-attested, content-addressed review records for diagnostic histories.

Review records are separate from immutable history artifacts. Their hashes
provide integrity checking, not reviewer authentication or a digital signature.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from edgar_moe.forward.diagnostic_history import (
    DiagnosticHistoryError,
    verify_forward_diagnostic_history,
)
from edgar_moe.utils.timestamps import parse_aware_timestamp

REVIEW_VERSION = 1
MAX_REVIEW_BYTES = 64_000

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVIEWER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_DECISIONS = frozenset({"acknowledged", "follow_up_required"})
_REASON_CODES = frozenset(
    {
        "history_status_needs_follow_up",
        "insufficient_history",
        "maturity_or_coverage_concern",
        "metric_anomaly",
        "provenance_question",
        "snapshot_overlap_or_dependence_question",
        "other_follow_up",
    }
)
_ACKNOWLEDGEMENT_FIELDS = frozenset(
    {
        "collection_status_is_not_performance_readiness",
        "counts_maturity_coverage_and_metrics_reviewed",
        "snapshot_independence_is_not_assessed",
        "short_horizon_is_not_official_20_session_evaluation",
        "v1_is_immutable_and_automatic_retraining_is_disabled",
        "review_does_not_authorize_promotion_or_retraining",
    }
)
_REVIEW_KEYS = frozenset(
    {
        "review_version",
        "scope",
        "research_only",
        "v1_immutable",
        "automatic_retraining",
        "promotion_eligible",
        "source_history",
        "reviewer_id",
        "reviewed_at",
        "decision",
        "reason_codes",
        "acknowledgements",
        "disclaimer",
        "review_sha256",
    }
)
_SOURCE_HISTORY_KEYS = frozenset(
    {
        "history_sha256",
        "history_version",
        "status",
        "report_count",
        "horizon_sessions",
        "latest_as_of",
    }
)
_DISCLAIMER = (
    "This is a self-attested review of one content-addressed forward diagnostic history. "
    "The review does not establish snapshot independence, predictive performance, or "
    "investment suitability; it is not promotion or retraining authorization. The SHA-256 "
    "digest detects content changes but does not authenticate the reviewer or provide a "
    "digital signature. The official 20-session evaluation and frozen v1 model remain "
    "unchanged."
)


class DiagnosticHistoryReviewError(ValueError):
    """Raised when a diagnostic-history review record violates its contract."""


def build_forward_diagnostic_history_review(
    history: Mapping[str, Any],
    *,
    reviewer_id: str,
    decision: str,
    summary_reviewed: bool,
    limitations_acknowledged: bool,
    reason_codes: Sequence[str] = (),
    reviewed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Build a review attestation bound to one verified history artifact.

    The reviewer identity is self-reported. No answer text, raw observations,
    forecast/event identifiers, source paths, or free-form notes are accepted.
    """
    try:
        verify_forward_diagnostic_history(history)
    except DiagnosticHistoryError as error:
        raise DiagnosticHistoryReviewError(str(error)) from error

    if not isinstance(reviewer_id, str) or not _REVIEWER_ID.fullmatch(reviewer_id):
        raise DiagnosticHistoryReviewError(
            "reviewer_id must be a short non-email identifier using letters, digits, dot, "
            "underscore, or hyphen"
        )
    if not isinstance(decision, str) or decision not in _DECISIONS:
        raise DiagnosticHistoryReviewError("decision must be acknowledged or follow_up_required")
    if summary_reviewed is not True:
        raise DiagnosticHistoryReviewError(
            "summary_reviewed must explicitly confirm the counts, maturity, coverage, and metrics"
        )
    if limitations_acknowledged is not True:
        raise DiagnosticHistoryReviewError(
            "limitations_acknowledged must explicitly confirm the research-only boundaries"
        )
    if history["status"] != "ready" and decision == "acknowledged":
        raise DiagnosticHistoryReviewError("non-ready history requires follow_up_required")
    normalized_reasons = _normalize_reason_codes(reason_codes)
    if decision == "acknowledged" and normalized_reasons:
        raise DiagnosticHistoryReviewError("acknowledged reviews cannot include follow-up reasons")
    if decision == "follow_up_required" and not normalized_reasons:
        raise DiagnosticHistoryReviewError("follow_up_required needs at least one reason code")

    timestamp = _parse_timestamp(
        datetime.now(UTC) if reviewed_at is None else reviewed_at, "reviewed_at"
    )
    source_history = {
        "history_sha256": history["history_sha256"],
        "history_version": history["history_version"],
        "status": history["status"],
        "report_count": history["report_count"],
        "horizon_sessions": history["horizon_sessions"],
        "latest_as_of": history["latest_as_of"],
    }
    payload: dict[str, Any] = {
        "review_version": REVIEW_VERSION,
        "scope": "forward_diagnostic_history_review",
        "research_only": True,
        "v1_immutable": True,
        "automatic_retraining": False,
        "promotion_eligible": False,
        "source_history": source_history,
        "reviewer_id": reviewer_id,
        "reviewed_at": timestamp.isoformat(),
        "decision": decision,
        "reason_codes": normalized_reasons,
        "acknowledgements": {
            field: (
                summary_reviewed
                if field == "counts_maturity_coverage_and_metrics_reviewed"
                else limitations_acknowledged
            )
            for field in sorted(_ACKNOWLEDGEMENT_FIELDS)
        },
        "disclaimer": _DISCLAIMER,
    }
    payload["review_sha256"] = _content_hash(payload)
    verify_forward_diagnostic_history_review(payload, history=history)
    return payload


def verify_forward_diagnostic_history_review(
    review: Mapping[str, Any], *, history: Mapping[str, Any] | None = None
) -> None:
    """Verify review integrity and, optionally, its association to a history."""
    if not isinstance(review, Mapping):
        raise DiagnosticHistoryReviewError("review record must be a JSON object")
    unknown = sorted(str(key) for key in review if key not in _REVIEW_KEYS)
    if unknown:
        raise DiagnosticHistoryReviewError(
            "review record contains unknown fields: " + ", ".join(unknown)
        )
    missing = sorted(key for key in _REVIEW_KEYS if key not in review)
    if missing:
        raise DiagnosticHistoryReviewError("review record is missing fields: " + ", ".join(missing))
    version = review.get("review_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != REVIEW_VERSION:
        raise DiagnosticHistoryReviewError("review_version is unsupported")
    if review.get("scope") != "forward_diagnostic_history_review":
        raise DiagnosticHistoryReviewError("review scope is invalid")
    if review.get("research_only") is not True:
        raise DiagnosticHistoryReviewError("review record must be research-only")
    if review.get("v1_immutable") is not True:
        raise DiagnosticHistoryReviewError("review record must preserve v1 immutability")
    if review.get("automatic_retraining") is not False:
        raise DiagnosticHistoryReviewError("review record cannot enable automatic retraining")
    if review.get("promotion_eligible") is not False:
        raise DiagnosticHistoryReviewError("review record cannot authorize promotion")
    if review.get("disclaimer") != _DISCLAIMER:
        raise DiagnosticHistoryReviewError("review disclaimer is invalid")

    source_history = review.get("source_history")
    if not isinstance(source_history, Mapping):
        raise DiagnosticHistoryReviewError("source_history must be an object")
    _validate_source_history(source_history)
    _validate_reviewer_id(review.get("reviewer_id"))
    reviewed_at = _parse_timestamp(review.get("reviewed_at"), "reviewed_at")
    if review.get("reviewed_at") != reviewed_at.isoformat():
        raise DiagnosticHistoryReviewError("reviewed_at must use canonical UTC ISO-8601 format")
    decision = review.get("decision")
    if not isinstance(decision, str) or decision not in _DECISIONS:
        raise DiagnosticHistoryReviewError("decision is invalid")
    if source_history["status"] != "ready" and decision == "acknowledged":
        raise DiagnosticHistoryReviewError("non-ready history requires follow_up_required")
    reasons = _normalize_reason_codes(review.get("reason_codes"))
    if list(reasons) != review.get("reason_codes"):
        raise DiagnosticHistoryReviewError("reason_codes must be unique and sorted")
    if decision == "acknowledged" and reasons:
        raise DiagnosticHistoryReviewError("acknowledged reviews cannot include follow-up reasons")
    if decision == "follow_up_required" and not reasons:
        raise DiagnosticHistoryReviewError("follow_up_required needs at least one reason code")
    acknowledgements = review.get("acknowledgements")
    if not isinstance(acknowledgements, Mapping):
        raise DiagnosticHistoryReviewError("acknowledgements must be an object")
    if set(acknowledgements) != _ACKNOWLEDGEMENT_FIELDS or any(
        value is not True for value in acknowledgements.values()
    ):
        raise DiagnosticHistoryReviewError("all required review acknowledgements must be true")

    expected_digest = review.get("review_sha256")
    if not isinstance(expected_digest, str) or not _SHA256.fullmatch(expected_digest):
        raise DiagnosticHistoryReviewError("review record is missing a SHA-256 review_sha256")
    unsigned = dict(review)
    unsigned.pop("review_sha256", None)
    if _content_hash(unsigned) != expected_digest:
        raise DiagnosticHistoryReviewError("review record content hash mismatch")

    if history is not None:
        try:
            verify_forward_diagnostic_history(history)
        except DiagnosticHistoryError as error:
            raise DiagnosticHistoryReviewError(str(error)) from error
        expected_source = {
            "history_sha256": history["history_sha256"],
            "history_version": history["history_version"],
            "status": history["status"],
            "report_count": history["report_count"],
            "horizon_sessions": history["horizon_sessions"],
            "latest_as_of": history["latest_as_of"],
        }
        if dict(source_history) != expected_source:
            raise DiagnosticHistoryReviewError("review record does not match the supplied history")


def read_forward_diagnostic_history_review(path: Path) -> dict[str, Any]:
    """Read and verify a bounded review record without printing its contents."""
    try:
        if path.stat().st_size > MAX_REVIEW_BYTES:
            raise DiagnosticHistoryReviewError("review record exceeds the size limit")
        payload = orjson.loads(path.read_bytes())
    except DiagnosticHistoryReviewError:
        raise
    except (OSError, orjson.JSONDecodeError) as error:
        raise DiagnosticHistoryReviewError("review record is unavailable") from error
    if not isinstance(payload, dict):
        raise DiagnosticHistoryReviewError("review record must be a JSON object")
    verify_forward_diagnostic_history_review(payload)
    return payload


def write_forward_diagnostic_history_review(path: Path, review: Mapping[str, Any]) -> None:
    """Atomically create a private review artifact without overwriting a prior record."""
    verify_forward_diagnostic_history_review(review)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(orjson.dumps(review, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
        # A same-directory hard link atomically fails when the destination exists.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_source_history(source: Mapping[str, Any]) -> None:
    unknown = sorted(str(key) for key in source if key not in _SOURCE_HISTORY_KEYS)
    if unknown:
        raise DiagnosticHistoryReviewError(
            "source_history contains unknown fields: " + ", ".join(unknown)
        )
    missing = sorted(key for key in _SOURCE_HISTORY_KEYS if key not in source)
    if missing:
        raise DiagnosticHistoryReviewError(
            "source_history is missing fields: " + ", ".join(missing)
        )
    digest = source.get("history_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise DiagnosticHistoryReviewError("source_history history_sha256 is invalid")
    version = source.get("history_version")
    if isinstance(version, bool) or not isinstance(version, int) or version not in {1, 2, 3}:
        raise DiagnosticHistoryReviewError("source_history history_version is invalid")
    status = source.get("status")
    if not isinstance(status, str) or status not in {
        "insufficient_history",
        "review_required",
        "ready",
    }:
        raise DiagnosticHistoryReviewError("source_history status is invalid")
    report_count = source.get("report_count")
    if (
        isinstance(report_count, bool)
        or not isinstance(report_count, int)
        or not 1 <= report_count <= 128
    ):
        raise DiagnosticHistoryReviewError("source_history report_count is invalid")
    horizon = source.get("horizon_sessions")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 2 <= horizon < 20:
        raise DiagnosticHistoryReviewError("source_history horizon_sessions is invalid")
    latest_as_of = _parse_timestamp(source.get("latest_as_of"), "source_history latest_as_of")
    if source.get("latest_as_of") != latest_as_of.isoformat():
        raise DiagnosticHistoryReviewError("source_history latest_as_of is not canonical UTC")


def _validate_reviewer_id(value: object) -> None:
    if not isinstance(value, str) or not _REVIEWER_ID.fullmatch(value):
        raise DiagnosticHistoryReviewError("reviewer_id is invalid")


def _normalize_reason_codes(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DiagnosticHistoryReviewError("reason_codes must be a list of stable reason codes")
    reasons = list(value)
    if any(not isinstance(item, str) or item not in _REASON_CODES for item in reasons):
        raise DiagnosticHistoryReviewError("reason_codes contains an unsupported code")
    if len(set(reasons)) != len(reasons):
        raise DiagnosticHistoryReviewError("reason_codes cannot contain duplicates")
    return sorted(reasons)


def _parse_timestamp(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        value = value.isoformat()
    try:
        return parse_aware_timestamp(value)
    except ValueError as error:
        raise DiagnosticHistoryReviewError(f"{label} {error}") from error


def _content_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(dict(payload), option=orjson.OPT_SORT_KEYS)).hexdigest()
