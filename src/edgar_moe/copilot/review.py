"""Append-only human review history for private copilot benchmark reports."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import orjson

from edgar_moe.utils.timestamps import parse_aware_timestamp

from .policy import validate_agent_identity


class ReviewInputError(ValueError):
    """Raised when a benchmark, review batch, or history violates its contract."""


_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVIEWER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,79}$")
_RUBRIC_FIELDS = ("grounding", "citations", "safety", "prose")
_DECISIONS = frozenset({"accept", "revise", "reject"})
_RUBRIC_VALUES = frozenset({"pass", "fail"})
_REVIEW_CODES = frozenset(
    {
        "citation_mismatch",
        "missing_citation",
        "other",
        "scope_violation",
        "unclear_prose",
        "unsafe_instruction",
        "unsupported_claim",
    }
)
_MAX_ENTRIES = 256


def append_copilot_reviews(
    benchmark: Mapping[str, Any],
    review_batch: Mapping[str, Any],
    existing_history: Mapping[str, Any] | None = None,
    *,
    minimum_reviews: int = 4,
) -> dict[str, Any]:
    """Append reviewed benchmark cases and return a content-addressed history.

    The benchmark and review batch are treated as private input. The returned
    history contains only case identity, answer hashes, rubric decisions, and
    review metadata; it never copies questions, answer text, or provider data.
    """

    if not 1 <= minimum_reviews <= _MAX_ENTRIES:
        raise ReviewInputError(f"minimum_reviews must be between 1 and {_MAX_ENTRIES}")
    benchmark_info = _validate_benchmark(benchmark)
    batch = _parse_review_batch(review_batch, benchmark_info)

    if existing_history is None:
        prior_entries: list[dict[str, Any]] = []
    else:
        prior = _validate_history(existing_history)
        if prior["corpus_id"] != benchmark_info["corpus_id"]:
            raise ReviewInputError("history corpus_id does not match the benchmark")
        if prior["corpus_sha256"] != benchmark_info["corpus_sha256"]:
            raise ReviewInputError("history corpus_sha256 does not match the benchmark")
        if prior["minimum_reviews"] != minimum_reviews:
            raise ReviewInputError("minimum_reviews cannot change an existing history")
        prior_entries = [dict(entry) for entry in prior["entries"]]

    seen_records = {(entry["case_id"], entry["answer_sha256"]) for entry in prior_entries}
    last_reviewed_at = _entry_timestamp(prior_entries[-1]) if prior_entries else None
    previous_entry_sha256 = prior_entries[-1]["entry_sha256"] if prior_entries else None
    new_entries: list[dict[str, Any]] = []
    for item in batch:
        record_key = (item["case_id"], item["answer_sha256"])
        if record_key in seen_records:
            raise ReviewInputError(
                f"duplicate answer review for case {item['case_id']} and answer hash"
            )
        reviewed_at = _parse_timestamp(item["reviewed_at"], "reviewed_at")
        if last_reviewed_at is not None and reviewed_at < last_reviewed_at:
            raise ReviewInputError("reviewed_at values must be append-only and chronological")
        entry = {
            "sequence": len(prior_entries) + len(new_entries) + 1,
            "previous_entry_sha256": previous_entry_sha256,
            "benchmark_sha256": benchmark_info["benchmark_sha256"],
            "case_id": item["case_id"],
            "answer_sha256": item["answer_sha256"],
            "reviewer": item["reviewer"],
            "reviewed_at": _isoformat(reviewed_at),
            "grounding": item["grounding"],
            "citations": item["citations"],
            "safety": item["safety"],
            "prose": item["prose"],
            "decision": item["decision"],
            "review_codes": item["review_codes"],
        }
        entry["entry_sha256"] = _content_hash(entry)
        new_entries.append(entry)
        seen_records.add(record_key)
        last_reviewed_at = reviewed_at
        previous_entry_sha256 = entry["entry_sha256"]

    entries = [*prior_entries, *new_entries]
    return _build_history(
        corpus_id=benchmark_info["corpus_id"],
        corpus_sha256=benchmark_info["corpus_sha256"],
        minimum_reviews=minimum_reviews,
        entries=entries,
    )


def verify_copilot_review_history(history: Mapping[str, Any]) -> None:
    """Verify a review history hash, immutable scope, entries, and aggregates."""

    _validate_history(history)


def write_copilot_review_history(path: Path, history: Mapping[str, Any]) -> None:
    """Atomically write a verified review history without partial JSON."""

    verify_copilot_review_history(history)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(orjson.dumps(history, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    temporary.replace(path)


def benchmark_sha256(benchmark: Mapping[str, Any]) -> str:
    """Return the digest pinned into a review entry for one benchmark report."""

    return cast(str, _validate_benchmark(benchmark)["benchmark_sha256"])


def _validate_benchmark(
    benchmark: Mapping[str, Any],
) -> dict[str, Any]:
    if benchmark.get("schema_version") != 1:
        raise ReviewInputError("benchmark schema_version must be 1")
    corpus_id = _identifier(benchmark.get("corpus_id"), "benchmark corpus_id")
    corpus_sha256 = _digest(benchmark.get("corpus_sha256"), "benchmark corpus_sha256")
    if benchmark.get("research_only") is True:
        raise ReviewInputError("benchmark aggregate must not declare answer text or research_only")
    if "answer" in benchmark or "question" in benchmark:
        raise ReviewInputError("benchmark aggregate must not contain answer text or questions")
    benchmark_metadata = benchmark.get("benchmark")
    if not isinstance(benchmark_metadata, Mapping):
        raise ReviewInputError("benchmark aggregate is missing benchmark metadata")
    if benchmark_metadata.get("provider_contacted") is not True:
        raise ReviewInputError("benchmark provider_contacted must be true for human review")
    _non_empty_string(benchmark_metadata.get("provider"), "benchmark provider")
    _non_empty_string(benchmark_metadata.get("model"), "benchmark model")
    agent_identity_status = benchmark.get("agent_identity_status", "legacy")
    if agent_identity_status not in {"consistent", "legacy", "mixed"}:
        raise ReviewInputError("benchmark agent_identity_status is invalid")
    agent_identity = benchmark.get("agent_identity")
    if agent_identity_status == "consistent":
        try:
            validate_agent_identity(agent_identity)
        except ValueError as error:
            raise ReviewInputError(f"benchmark {error}") from error
    elif agent_identity is not None:
        raise ReviewInputError(
            "benchmark agent_identity must be omitted or null unless status is consistent"
        )
    cases_value = benchmark.get("cases")
    if not isinstance(cases_value, list) or not 1 <= len(cases_value) <= _MAX_ENTRIES:
        raise ReviewInputError(f"benchmark cases must contain 1-{_MAX_ENTRIES} entries")
    cases: dict[str, dict[str, Any]] = {}
    for index, raw_case in enumerate(cases_value, start=1):
        if not isinstance(raw_case, Mapping):
            raise ReviewInputError(f"benchmark case {index} must be an object")
        case_id = _identifier(raw_case.get("case_id"), f"benchmark case {index} case_id")
        if case_id in cases:
            raise ReviewInputError(f"benchmark contains duplicate case_id: {case_id}")
        if "answer" in raw_case or "question" in raw_case:
            raise ReviewInputError("benchmark aggregate must not contain answer text or questions")
        cases[case_id] = {
            "case_id": case_id,
            "answer_sha256": _digest(
                raw_case.get("answer_sha256"), f"benchmark case {case_id} answer_sha256"
            ),
        }
    selected_case_ids = benchmark_metadata.get("selected_case_ids")
    if not isinstance(selected_case_ids, list) or not all(
        isinstance(value, str) and _CASE_ID.fullmatch(value) for value in selected_case_ids
    ):
        raise ReviewInputError("benchmark selected_case_ids must be lowercase identifiers")
    missing_case_ids = benchmark.get("missing_case_ids")
    if not isinstance(missing_case_ids, list) or not all(
        isinstance(value, str) and _CASE_ID.fullmatch(value) for value in missing_case_ids
    ):
        raise ReviewInputError("benchmark missing_case_ids must be lowercase identifiers")
    if len(set(selected_case_ids)) != len(selected_case_ids):
        raise ReviewInputError("benchmark selected_case_ids must not contain duplicates")
    if len(set(missing_case_ids)) != len(missing_case_ids):
        raise ReviewInputError("benchmark missing_case_ids must not contain duplicates")
    if set(missing_case_ids) & set(cases):
        raise ReviewInputError("benchmark missing_case_ids overlap completed cases")
    if set(selected_case_ids) != set(cases) | set(missing_case_ids):
        raise ReviewInputError("benchmark selected case IDs do not match its cases and missing IDs")
    if not isinstance(benchmark.get("case_count"), int) or benchmark["case_count"] != len(cases):
        raise ReviewInputError("benchmark case_count does not match its cases")
    if not isinstance(benchmark.get("complete"), bool):
        raise ReviewInputError("benchmark complete must be a boolean")
    if benchmark["complete"] != (not missing_case_ids):
        raise ReviewInputError("benchmark complete does not match missing_case_ids")
    return {
        "corpus_id": corpus_id,
        "corpus_sha256": corpus_sha256,
        "benchmark_sha256": _content_hash(benchmark),
        "cases": cases,
    }


def _parse_review_batch(
    review_batch: Mapping[str, Any], benchmark_info: Mapping[str, Any]
) -> list[dict[str, Any]]:
    allowed = {"schema_version", "corpus_id", "corpus_sha256", "benchmark_sha256", "reviews"}
    unknown = sorted(set(review_batch) - allowed)
    if unknown:
        raise ReviewInputError(f"review batch contains unknown fields: {', '.join(unknown)}")
    if review_batch.get("schema_version") != 1:
        raise ReviewInputError("review batch schema_version must be 1")
    if review_batch.get("corpus_id") != benchmark_info["corpus_id"]:
        raise ReviewInputError("review batch corpus_id does not match the benchmark")
    if review_batch.get("corpus_sha256") != benchmark_info["corpus_sha256"]:
        raise ReviewInputError("review batch corpus_sha256 does not match the benchmark")
    supplied_benchmark_sha256 = review_batch.get("benchmark_sha256")
    if (
        supplied_benchmark_sha256 is not None
        and supplied_benchmark_sha256 != benchmark_info["benchmark_sha256"]
    ):
        raise ReviewInputError("review batch benchmark_sha256 does not match the benchmark")
    raw_reviews = review_batch.get("reviews")
    if not isinstance(raw_reviews, list) or not 1 <= len(raw_reviews) <= _MAX_ENTRIES:
        raise ReviewInputError(f"review batch reviews must contain 1-{_MAX_ENTRIES} entries")
    cases = benchmark_info["cases"]
    parsed: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    previous_reviewed_at: datetime | None = None
    for index, raw_review in enumerate(raw_reviews, start=1):
        if not isinstance(raw_review, Mapping):
            raise ReviewInputError(f"review {index} must be an object")
        allowed_review = {
            "answer_sha256",
            "case_id",
            "citations",
            "decision",
            "grounding",
            "prose",
            "review_codes",
            "reviewed_at",
            "reviewer",
            "safety",
        }
        unknown_review = sorted(set(raw_review) - allowed_review)
        if unknown_review:
            raise ReviewInputError(
                f"review {index} contains unknown fields: {', '.join(unknown_review)}"
            )
        case_id = _identifier(raw_review.get("case_id"), f"review {index} case_id")
        if case_id in seen_case_ids:
            raise ReviewInputError(f"review batch contains duplicate case_id: {case_id}")
        benchmark_case = cases.get(case_id)
        if benchmark_case is None:
            raise ReviewInputError(f"review references an uncompleted benchmark case: {case_id}")
        answer_sha256 = _digest(raw_review.get("answer_sha256"), f"review {case_id} answer_sha256")
        if answer_sha256 != benchmark_case["answer_sha256"]:
            raise ReviewInputError(f"review answer hash does not match benchmark case: {case_id}")
        reviewer = raw_review.get("reviewer")
        if not isinstance(reviewer, str) or not _REVIEWER.fullmatch(reviewer):
            raise ReviewInputError(f"review {case_id} reviewer must be a safe identifier")
        reviewed_at = _parse_timestamp(
            raw_review.get("reviewed_at"), f"review {case_id} reviewed_at"
        )
        if previous_reviewed_at is not None and reviewed_at < previous_reviewed_at:
            raise ReviewInputError("review batch reviewed_at values must be chronological")
        rubric: dict[str, str] = {}
        for field in _RUBRIC_FIELDS:
            value = raw_review.get(field)
            if value not in _RUBRIC_VALUES:
                raise ReviewInputError(f"review {case_id} {field} must be pass or fail")
            rubric[field] = str(value)
        decision = raw_review.get("decision")
        if decision not in _DECISIONS:
            raise ReviewInputError(f"review {case_id} decision must be accept, revise, or reject")
        failed_rubric = any(value == "fail" for value in rubric.values())
        if (not failed_rubric and decision != "accept") or (failed_rubric and decision == "accept"):
            raise ReviewInputError(f"review {case_id} decision does not match rubric results")
        if rubric["safety"] == "fail" and decision != "reject":
            raise ReviewInputError(f"review {case_id} safety failure requires reject decision")
        review_codes = raw_review.get("review_codes", [])
        if not isinstance(review_codes, list) or len(review_codes) > 8:
            raise ReviewInputError(f"review {case_id} review_codes must contain at most 8 values")
        if not all(isinstance(code, str) and code in _REVIEW_CODES for code in review_codes):
            raise ReviewInputError(f"review {case_id} has an unsupported review code")
        if failed_rubric and not review_codes:
            raise ReviewInputError(f"review {case_id} must include a code for a failed rubric")
        if len(set(review_codes)) != len(review_codes):
            raise ReviewInputError(f"review {case_id} review_codes must not contain duplicates")
        parsed.append(
            {
                "case_id": case_id,
                "answer_sha256": answer_sha256,
                "reviewer": reviewer,
                "reviewed_at": _isoformat(reviewed_at),
                **rubric,
                "decision": str(decision),
                "review_codes": list(review_codes),
            }
        )
        seen_case_ids.add(case_id)
        previous_reviewed_at = reviewed_at
    return parsed


def _build_history(
    *,
    corpus_id: str,
    corpus_sha256: str,
    minimum_reviews: int,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    accepted_count = sum(entry["decision"] == "accept" for entry in entries)
    revise_count = sum(entry["decision"] == "revise" for entry in entries)
    rejected_count = sum(entry["decision"] == "reject" for entry in entries)
    if rejected_count:
        status = "rejected"
    elif revise_count:
        status = "review_required"
    elif len(entries) < minimum_reviews:
        status = "insufficient_history"
    else:
        status = "accepted"
    history: dict[str, Any] = {
        "schema_version": 1,
        "scope": "research_copilot_review_history",
        "v1_immutable": True,
        "automatic_retraining": False,
        "research_only": True,
        "corpus_id": corpus_id,
        "corpus_sha256": corpus_sha256,
        "minimum_reviews": minimum_reviews,
        "entry_count": len(entries),
        "accepted_count": accepted_count,
        "revise_count": revise_count,
        "rejected_count": rejected_count,
        "status": status,
        "last_reviewed_at": entries[-1]["reviewed_at"] if entries else None,
        "entries": entries,
        "disclaimer": (
            "Human review of evidence-grounded copilot outputs only; this history does not "
            "establish statistical validity, investment suitability, or permission to retrain."
        ),
    }
    history["history_sha256"] = _content_hash(history)
    return history


def _validate_history(history: Mapping[str, Any]) -> dict[str, Any]:
    if history.get("schema_version") != 1:
        raise ReviewInputError("review history schema_version must be 1")
    if history.get("scope") != "research_copilot_review_history":
        raise ReviewInputError("review history has an unsupported scope")
    if history.get("v1_immutable") is not True or history.get("automatic_retraining") is not False:
        raise ReviewInputError("review history violates the frozen-model contract")
    if history.get("research_only") is not True:
        raise ReviewInputError("review history must be research_only")
    corpus_id = _identifier(history.get("corpus_id"), "review history corpus_id")
    corpus_sha256 = _digest(history.get("corpus_sha256"), "review history corpus_sha256")
    minimum_reviews = history.get("minimum_reviews")
    if (
        isinstance(minimum_reviews, bool)
        or not isinstance(minimum_reviews, int)
        or not 1 <= minimum_reviews <= _MAX_ENTRIES
    ):
        raise ReviewInputError(
            f"review history minimum_reviews must be between 1 and {_MAX_ENTRIES}"
        )
    entries = history.get("entries")
    if not isinstance(entries, list) or len(entries) > _MAX_ENTRIES:
        raise ReviewInputError(
            f"review history entries must contain at most {_MAX_ENTRIES} entries"
        )
    previous_reviewed_at: datetime | None = None
    seen_records: set[tuple[str, str]] = set()
    for expected_sequence, raw_entry in enumerate(entries, start=1):
        if not isinstance(raw_entry, Mapping):
            raise ReviewInputError(f"review history entry {expected_sequence} must be an object")
        if raw_entry.get("sequence") != expected_sequence:
            raise ReviewInputError("review history entry sequences must be contiguous")
        allowed_entry = {
            "answer_sha256",
            "benchmark_sha256",
            "case_id",
            "citations",
            "decision",
            "entry_sha256",
            "grounding",
            "previous_entry_sha256",
            "prose",
            "review_codes",
            "reviewed_at",
            "reviewer",
            "safety",
            "sequence",
        }
        unknown_entry = sorted(set(raw_entry) - allowed_entry)
        if unknown_entry:
            raise ReviewInputError(
                f"review history entry contains unknown fields: {', '.join(unknown_entry)}"
            )
        _digest(raw_entry.get("benchmark_sha256"), "review history benchmark_sha256")
        case_id = _identifier(raw_entry.get("case_id"), "review history case_id")
        answer_sha256 = _digest(raw_entry.get("answer_sha256"), "review history answer_sha256")
        record_key = (case_id, answer_sha256)
        if record_key in seen_records:
            raise ReviewInputError("review history contains a duplicate answer review")
        seen_records.add(record_key)
        reviewed_at = _parse_timestamp(raw_entry.get("reviewed_at"), "review history reviewed_at")
        if previous_reviewed_at is not None and reviewed_at < previous_reviewed_at:
            raise ReviewInputError("review history entries must be chronological")
        previous_reviewed_at = reviewed_at
        reviewer = raw_entry.get("reviewer")
        if not isinstance(reviewer, str) or not _REVIEWER.fullmatch(reviewer):
            raise ReviewInputError("review history contains an unsafe reviewer identifier")
        unsigned_entry = dict(raw_entry)
        entry_sha256 = unsigned_entry.pop("entry_sha256", None)
        if _digest(entry_sha256, "review history entry_sha256") != _content_hash(unsigned_entry):
            raise ReviewInputError("review history entry hash mismatch")
        previous_entry_sha256 = raw_entry.get("previous_entry_sha256")
        if expected_sequence == 1:
            if previous_entry_sha256 is not None:
                raise ReviewInputError("first review history entry must not have a predecessor")
        elif _digest(previous_entry_sha256, "review history previous_entry_sha256") != entries[
            expected_sequence - 2
        ].get("entry_sha256"):
            raise ReviewInputError("review history entry chain mismatch")
        for field in _RUBRIC_FIELDS:
            if raw_entry.get(field) not in _RUBRIC_VALUES:
                raise ReviewInputError("review history contains an invalid rubric value")
        if raw_entry.get("decision") not in _DECISIONS:
            raise ReviewInputError("review history contains an invalid decision")
        review_codes = raw_entry.get("review_codes")
        if (
            not isinstance(review_codes, list)
            or len(review_codes) > 8
            or not all(isinstance(code, str) and code in _REVIEW_CODES for code in review_codes)
        ):
            raise ReviewInputError("review history contains an invalid review code")
        failed_rubric = any(raw_entry.get(field) == "fail" for field in _RUBRIC_FIELDS)
        if (not failed_rubric and raw_entry["decision"] != "accept") or (
            failed_rubric and raw_entry["decision"] == "accept"
        ):
            raise ReviewInputError("review history decision does not match its rubric")
        if raw_entry["safety"] == "fail" and raw_entry["decision"] != "reject":
            raise ReviewInputError("review history safety failure requires reject decision")
        if failed_rubric and not review_codes:
            raise ReviewInputError("review history failed rubric has no review code")
        if len(set(review_codes)) != len(review_codes):
            raise ReviewInputError("review history contains duplicate review codes")
    expected_counts = {
        "entry_count": len(entries),
        "accepted_count": sum(entry["decision"] == "accept" for entry in entries),
        "revise_count": sum(entry["decision"] == "revise" for entry in entries),
        "rejected_count": sum(entry["decision"] == "reject" for entry in entries),
    }
    for field, expected in expected_counts.items():
        if history.get(field) != expected:
            raise ReviewInputError(f"review history {field} does not match its entries")
    expected_status = _history_status(
        expected_counts["revise_count"],
        expected_counts["rejected_count"],
        len(entries),
        minimum_reviews,
    )
    if history.get("status") != expected_status:
        raise ReviewInputError("review history status does not match its entries")
    expected_last = entries[-1]["reviewed_at"] if entries else None
    if history.get("last_reviewed_at") != expected_last:
        raise ReviewInputError("review history last_reviewed_at does not match its entries")
    expected_hash = history.get("history_sha256")
    if _digest(expected_hash, "review history history_sha256") != _content_hash(
        {key: value for key, value in history.items() if key != "history_sha256"}
    ):
        raise ReviewInputError("review history hash mismatch")
    return {
        "corpus_id": corpus_id,
        "corpus_sha256": corpus_sha256,
        "minimum_reviews": minimum_reviews,
        "entries": entries,
    }


def _history_status(
    revise_count: int, rejected_count: int, count: int, minimum_reviews: int
) -> str:
    if rejected_count:
        return "rejected"
    if revise_count:
        return "review_required"
    if count < minimum_reviews:
        return "insufficient_history"
    return "accepted"


def _entry_timestamp(entry: Mapping[str, Any]) -> datetime:
    return _parse_timestamp(entry.get("reviewed_at"), "reviewed_at")


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _CASE_ID.fullmatch(value):
        raise ReviewInputError(f"{label} must be a lowercase identifier")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ReviewInputError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewInputError(f"{label} must be a non-empty string")
    return value


def _parse_timestamp(value: object, label: str) -> datetime:
    try:
        return parse_aware_timestamp(value)
    except ValueError as error:
        raise ReviewInputError(f"{label} {error}") from error


def _isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _content_hash(value: object) -> str:
    return hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS)).hexdigest()
