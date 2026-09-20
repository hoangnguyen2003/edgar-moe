"""A safe readiness gate for relying on private research-copilot reports."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any

import orjson

from .review import ReviewInputError, benchmark_sha256, verify_copilot_review_history


class CopilotReadinessError(ValueError):
    """Raised when a benchmark or review history cannot form a readiness report."""


_SHA256_LENGTH = 64
_STATUSES = frozenset({"ready", "blocked", "review_required"})
_CHECK_STATUSES = frozenset({"passed", "failed"})
_REVIEW_CHECKS = frozenset(
    {"review_history_status", "review_history_minimum", "current_case_reviews"}
)
_CHECK_IDS = (
    "benchmark_complete",
    "benchmark_pass_rate",
    "benchmark_cases_passed",
    "corpus_identity",
    "review_history_status",
    "review_history_minimum",
    "current_case_reviews",
)
_READINESS_KEYS = frozenset(
    {
        "benchmark_case_count",
        "benchmark_sha256",
        "blocked_reasons",
        "checks",
        "corpus_id",
        "corpus_sha256",
        "disclaimer",
        "history_minimum_reviews",
        "missing_current_case_ids",
        "min_pass_rate",
        "readiness_sha256",
        "research_only",
        "reviewed_current_case_count",
        "schema_version",
        "scope",
        "status",
    }
)
_DISCLAIMER = (
    "Copilot readiness is a structural evidence gate; it does not establish answer truth, "
    "statistical validity, investment suitability, or permission to retrain."
)


def build_copilot_readiness(
    benchmark: Mapping[str, Any],
    history: Mapping[str, Any],
    *,
    min_pass_rate: float = 1.0,
) -> dict[str, Any]:
    """Build a hash-pinned readiness summary without retaining answer text.

    The benchmark and history validators remain the authority for their own
    schemas. This gate adds the cross-artifact checks required before an
    operator relies on the copilot: complete and passing benchmark, matching
    corpus identity, accepted review history, and a review for every current
    case/answer hash.
    """
    threshold = _pass_rate(min_pass_rate)
    try:
        benchmark_digest = benchmark_sha256(benchmark)
        verify_copilot_review_history(history)
    except ReviewInputError as error:
        raise CopilotReadinessError(str(error)) from error

    corpus_id = _required_text(benchmark.get("corpus_id"), "benchmark corpus_id")
    corpus_sha256 = _required_digest(benchmark.get("corpus_sha256"), "benchmark corpus_sha256")
    history_corpus_id = _required_text(history.get("corpus_id"), "history corpus_id")
    history_corpus_sha256 = _required_digest(
        history.get("corpus_sha256"), "history corpus_sha256"
    )

    cases = benchmark.get("cases")
    if not isinstance(cases, list) or not cases:
        raise CopilotReadinessError("benchmark cases must be a non-empty list")
    current_cases = _current_cases(cases)
    missing_case_ids = _missing_current_reviews(current_cases, history, benchmark_digest)
    pass_rate = _pass_rate(benchmark.get("pass_rate"), "benchmark pass_rate")
    case_count = _positive_int(benchmark.get("case_count"), "benchmark case_count")
    history_minimum = _positive_int(
        history.get("minimum_reviews"), "history minimum_reviews"
    )
    history_entry_count = _nonnegative_int(history.get("entry_count"), "history entry_count")
    benchmark_complete = benchmark.get("complete") is True and not benchmark.get(
        "missing_case_ids"
    )
    cases_passed = all(
        isinstance(case, Mapping)
        and case.get("passed") is True
        and isinstance(case.get("failures"), list)
        and not case["failures"]
        for case in cases
    )
    checks = [
        _check("benchmark_complete", benchmark_complete),
        _check("benchmark_pass_rate", pass_rate >= threshold),
        _check("benchmark_cases_passed", cases_passed),
        _check(
            "corpus_identity",
            corpus_id == history_corpus_id and corpus_sha256 == history_corpus_sha256,
        ),
        _check("review_history_status", history.get("status") == "accepted"),
        _check("review_history_minimum", history_entry_count >= history_minimum),
        _check("current_case_reviews", not missing_case_ids),
    ]
    failed_ids = [str(check["check_id"]) for check in checks if check["status"] == "failed"]
    core_failed = any(check_id not in _REVIEW_CHECKS for check_id in failed_ids)
    status = "ready" if not failed_ids else "blocked" if core_failed else "review_required"
    payload: dict[str, Any] = {
        "schema_version": 1,
        "scope": "research_copilot_readiness",
        "research_only": True,
        "status": status,
        "benchmark_sha256": benchmark_digest,
        "corpus_id": corpus_id,
        "corpus_sha256": corpus_sha256,
        "min_pass_rate": threshold,
        "history_minimum_reviews": history_minimum,
        "benchmark_case_count": case_count,
        "reviewed_current_case_count": len(current_cases) - len(missing_case_ids),
        "missing_current_case_ids": missing_case_ids,
        "checks": checks,
        "blocked_reasons": failed_ids,
        "disclaimer": _DISCLAIMER,
    }
    payload["readiness_sha256"] = _content_hash(payload)
    verify_copilot_readiness(payload)
    return payload


def verify_copilot_readiness(report: Mapping[str, Any]) -> None:
    """Verify a retained readiness summary without reopening private inputs."""
    if not isinstance(report, Mapping):
        raise CopilotReadinessError("copilot readiness must be a JSON object")
    unknown = sorted(str(key) for key in report if key not in _READINESS_KEYS)
    if unknown:
        raise CopilotReadinessError("copilot readiness contains unknown fields: " + ", ".join(unknown))
    missing = sorted(key for key in _READINESS_KEYS if key not in report)
    if missing:
        raise CopilotReadinessError("copilot readiness is missing fields: " + ", ".join(missing))
    if report.get("schema_version") != 1:
        raise CopilotReadinessError("copilot readiness schema_version must be 1")
    if report.get("scope") != "research_copilot_readiness":
        raise CopilotReadinessError("copilot readiness scope is invalid")
    if report.get("research_only") is not True:
        raise CopilotReadinessError("copilot readiness research_only must be true")
    if report.get("status") not in _STATUSES:
        raise CopilotReadinessError("copilot readiness status is invalid")
    if report.get("disclaimer") != _DISCLAIMER:
        raise CopilotReadinessError("copilot readiness disclaimer is invalid")
    _required_digest(report.get("benchmark_sha256"), "benchmark_sha256")
    _required_digest(report.get("corpus_sha256"), "corpus_sha256")
    _required_text(report.get("corpus_id"), "corpus_id")
    _pass_rate(report.get("min_pass_rate"))
    _positive_int(report.get("history_minimum_reviews"), "history_minimum_reviews")
    case_count = _positive_int(report.get("benchmark_case_count"), "benchmark_case_count")
    reviewed_count = _nonnegative_int(
        report.get("reviewed_current_case_count"), "reviewed_current_case_count"
    )
    if reviewed_count > case_count:
        raise CopilotReadinessError("reviewed_current_case_count exceeds benchmark_case_count")
    missing_ids = report.get("missing_current_case_ids")
    if not isinstance(missing_ids, list) or not all(
        isinstance(value, str) and value.strip() for value in missing_ids
    ):
        raise CopilotReadinessError("missing_current_case_ids must be a list of identifiers")
    if len(missing_ids) != case_count - reviewed_count:
        raise CopilotReadinessError("missing_current_case_ids count is inconsistent")
    checks = report.get("checks")
    if not isinstance(checks, list) or len(checks) != len(_CHECK_IDS):
        raise CopilotReadinessError("copilot readiness checks are incomplete")
    seen: set[str] = set()
    failed_ids: list[str] = []
    for check in checks:
        if not isinstance(check, Mapping) or set(check) != {"check_id", "status"}:
            raise CopilotReadinessError("copilot readiness checks contain unsupported fields")
        check_id = check.get("check_id")
        if check_id not in _CHECK_IDS or check_id in seen:
            raise CopilotReadinessError("copilot readiness check identifiers are invalid")
        seen.add(str(check_id))
        if check.get("status") not in _CHECK_STATUSES:
            raise CopilotReadinessError("copilot readiness check status is invalid")
        if check["status"] == "failed":
            failed_ids.append(str(check_id))
    if seen != set(_CHECK_IDS):
        raise CopilotReadinessError("copilot readiness check identifiers are incomplete")
    if report.get("blocked_reasons") != failed_ids:
        raise CopilotReadinessError("copilot readiness blocked_reasons do not match checks")
    expected_hash = _required_digest(report.get("readiness_sha256"), "readiness_sha256")
    unsigned = dict(report)
    unsigned.pop("readiness_sha256", None)
    if _content_hash(unsigned) != expected_hash:
        raise CopilotReadinessError("copilot readiness content hash mismatch")


def _current_cases(cases: list[object]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, Mapping):
            raise CopilotReadinessError(f"benchmark case {index} must be an object")
        case_id = _required_text(case.get("case_id"), f"benchmark case {index} case_id")
        answer_sha256 = _required_digest(
            case.get("answer_sha256"), f"benchmark case {case_id} answer_sha256"
        )
        result.add((case_id, answer_sha256))
    if len(result) != len(cases):
        raise CopilotReadinessError("benchmark cases contain duplicate case identities")
    return result


def _missing_current_reviews(
    current_cases: set[tuple[str, str]], history: Mapping[str, Any], benchmark_digest: str
) -> list[str]:
    entries = history.get("entries")
    if not isinstance(entries, list):
        raise CopilotReadinessError("history entries must be a list")
    reviewed = {
        (str(entry.get("case_id")), str(entry.get("answer_sha256")))
        for entry in entries
        if isinstance(entry, Mapping)
        and entry.get("benchmark_sha256") == benchmark_digest
        and entry.get("decision") == "accept"
    }
    return sorted(case_id for case_id, digest in current_cases if (case_id, digest) not in reviewed)


def _check(check_id: str, passed: bool) -> dict[str, str]:
    return {"check_id": check_id, "status": "passed" if passed else "failed"}


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CopilotReadinessError(f"{label} must be a non-empty string")
    return value


def _required_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != _SHA256_LENGTH or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise CopilotReadinessError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _pass_rate(value: object, label: str = "min_pass_rate") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CopilotReadinessError(f"{label} must be a number between 0 and 1")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0 <= numeric <= 1:
        raise CopilotReadinessError(f"{label} must be a number between 0 and 1")
    return numeric


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CopilotReadinessError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CopilotReadinessError(f"{label} must be a non-negative integer")
    return value


def _content_hash(value: object) -> str:
    return hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS)).hexdigest()
