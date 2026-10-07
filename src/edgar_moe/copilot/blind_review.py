"""Private, label-masked paired human review for copilot versus control.

Randomized A/B order hides explicit provider/model labels, but answer style can
still reveal an arm. This is not a guarantee of perfect reviewer blinding.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import secrets
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, cast

import orjson

from edgar_moe.utils.timestamps import parse_aware_timestamp

from .benchmark import ensure_private_output_outside_git
from .contracts import content_hash
from .evaluation import EvaluationCorpus
from .paired import compare_benchmarks
from .verification import verify_copilot_answer_report

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_RUBRIC_FIELDS = ("task_completion", "factuality", "citations", "safety")


class MaskedReviewError(ValueError):
    """A paired review input is missing, mismatched, or not safely structured."""


def prepare_masked_review(
    baseline_dir: Path,
    copilot_dir: Path,
    corpus: EvaluationCorpus,
    *,
    arm_a: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a private reviewer packet and a separately held A/B identity map."""
    baseline = read_private_json(baseline_dir / "evaluation.json")
    copilot = read_private_json(copilot_dir / "evaluation.json")
    comparison = compare_benchmarks(baseline, copilot)
    compared_cases = cast(list[dict[str, Any]], comparison["cases"])
    snapshot_sha256 = cast(str, comparison["snapshot_sha256"])
    expected_ids = [case.case_id for case in corpus.cases]
    if (
        comparison["corpus_id"] != corpus.corpus_id
        or comparison["corpus_sha256"] != corpus.sha256
        or [case["case_id"] for case in compared_cases] != expected_ids
        or any(
            case["baseline_pass"] is None or case["copilot_pass"] is None for case in compared_cases
        )
    ):
        raise MaskedReviewError("both complete arms must match the reviewed corpus")
    if arm_a is None:
        choices = ["baseline", "copilot"] * (len(expected_ids) // 2)
        if len(expected_ids) % 2:
            choices.append(secrets.choice(("baseline", "copilot")))
        secrets.SystemRandom().shuffle(choices)
        assignments = dict(zip(expected_ids, choices, strict=True))
    else:
        assignments = dict(arm_a)
        if set(assignments) != set(expected_ids) or any(
            value not in {"baseline", "copilot"} for value in assignments.values()
        ):
            raise MaskedReviewError("A/B assignments must cover the corpus exactly")

    packets: list[dict[str, Any]] = []
    mapping: list[dict[str, str]] = []
    for case in corpus.cases:
        baseline_answer = _load_verified_answer(
            baseline_dir, baseline, case.case_id, case.question, snapshot_sha256
        )
        copilot_answer = _load_verified_answer(
            copilot_dir, copilot, case.case_id, case.question, snapshot_sha256
        )
        by_arm = {"baseline": baseline_answer, "copilot": copilot_answer}
        a_name = assignments[case.case_id]
        b_name = "copilot" if a_name == "baseline" else "baseline"
        packets.append(
            {
                "case_id": case.case_id,
                "question": case.question,
                "A": _visible_answer(by_arm[a_name]),
                "B": _visible_answer(by_arm[b_name]),
            }
        )
        mapping.append({"case_id": case.case_id, "A": a_name, "B": b_name})
    packet: dict[str, Any] = {
        "schema_version": 1,
        "corpus_id": corpus.corpus_id,
        "corpus_sha256": corpus.sha256,
        "snapshot_sha256": comparison["snapshot_sha256"],
        "baseline_report_sha256": comparison["baseline_report_sha256"],
        "copilot_report_sha256": comparison["copilot_report_sha256"],
        "paired_comparison_sha256": content_hash(comparison),
        "cases": packets,
        "review_instruction": (
            "Score A and B independently against the same task. Labels are randomized; "
            "answer style may still reveal the arm. Do not inspect the mapping until scores are sealed."
        ),
    }
    key: dict[str, Any] = {
        "schema_version": 1,
        "packet_sha256": content_hash(packet),
        "assignments": mapping,
        # This stays with the sealed A/B mapping so latency, structural outcomes,
        # and provider usage cannot disclose the arm before human scoring.
        "paired_comparison": comparison,
    }
    return packet, key


def score_masked_review(
    packet: Mapping[str, Any], key: Mapping[str, Any], review: Mapping[str, Any]
) -> dict[str, Any]:
    """Unmask complete rubric judgments into a content-addressed safe report."""
    if (
        set(packet)
        != {
            "schema_version",
            "corpus_id",
            "corpus_sha256",
            "snapshot_sha256",
            "baseline_report_sha256",
            "copilot_report_sha256",
            "paired_comparison_sha256",
            "cases",
            "review_instruction",
        }
        or set(key) != {"schema_version", "packet_sha256", "assignments", "paired_comparison"}
        or set(review)
        != {"schema_version", "packet_sha256", "corpus_sha256", "reviewer", "reviewed_at", "cases"}
        or packet.get("schema_version") != 1
        or key.get("schema_version") != 1
        or review.get("schema_version") != 1
    ):
        raise MaskedReviewError("packet or mapping schema_version is invalid")
    packet_hash = content_hash(packet)
    if key.get("packet_sha256") != packet_hash or review.get("packet_sha256") != packet_hash:
        raise MaskedReviewError("review or mapping is not bound to this packet")
    corpus_id = packet.get("corpus_id")
    corpus_hash = packet.get("corpus_sha256")
    if (
        not isinstance(corpus_id, str)
        or not _ID.fullmatch(corpus_id)
        or not isinstance(corpus_hash, str)
        or not _SHA256.fullmatch(corpus_hash)
        or review.get("corpus_sha256") != corpus_hash
    ):
        raise MaskedReviewError("review corpus identity is invalid")
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, str) or not _ID.fullmatch(reviewer):
        raise MaskedReviewError("reviewer must be a short pseudonymous identifier")
    reviewed_at = review.get("reviewed_at")
    try:
        if not isinstance(reviewed_at, str):
            raise ValueError("timestamp must be a string")
        reviewed_datetime: datetime = parse_aware_timestamp(reviewed_at)
    except ValueError as error:
        raise MaskedReviewError("reviewed_at must be a timezone-aware timestamp") from error
    packet_cases = _case_map(packet.get("cases"), "packet")
    assignments = _case_map(key.get("assignments"), "mapping")
    judgments = _case_map(review.get("cases"), "review")
    if (
        not packet_cases
        or set(packet_cases) != set(assignments)
        or set(packet_cases) != set(judgments)
    ):
        raise MaskedReviewError("packet, mapping, and review must cover the same cases")
    paired_comparison = _validate_paired_comparison(packet, key, packet_cases)

    counts = {
        arm: {field: 0 for field in _RUBRIC_FIELDS} | {"quality_pass": 0}
        for arm in ("baseline", "copilot")
    }
    paired_rubric = {
        field: {"copilot_only": 0, "baseline_only": 0, "both": 0, "neither": 0}
        for field in _RUBRIC_FIELDS
    }
    paired = {"copilot_only": 0, "baseline_only": 0, "both": 0, "neither": 0}
    usefulness = {"copilot_higher": 0, "baseline_higher": 0, "tie": 0}
    case_scores: list[dict[str, Any]] = []
    for case_id in packet_cases:
        packet_case = packet_cases[case_id]
        if (
            set(packet_case) != {"case_id", "question", "A", "B"}
            or not isinstance(packet_case["question"], str)
            or not packet_case["question"].strip()
            or any(
                not isinstance(packet_case[label], dict)
                or set(packet_case[label]) != {"answer", "citations"}
                or not isinstance(packet_case[label]["answer"], str)
                or not isinstance(packet_case[label]["citations"], list)
                for label in ("A", "B")
            )
        ):
            raise MaskedReviewError("packet case schema is invalid")
        assignment = assignments[case_id]
        if (
            set(assignment) != {"case_id", "A", "B"}
            or not isinstance(assignment["A"], str)
            or not isinstance(assignment["B"], str)
            or {assignment["A"], assignment["B"]} != {"baseline", "copilot"}
        ):
            raise MaskedReviewError("A/B mapping is invalid")
        judgment = judgments[case_id]
        if set(judgment) != {"case_id", "A", "B"}:
            raise MaskedReviewError("review case rubric schema is invalid")
        scores = {assignment[label]: _parse_rating(judgment[label]) for label in ("A", "B")}
        quality = {}
        for arm, rating in scores.items():
            for field in _RUBRIC_FIELDS:
                counts[arm][field] += int(rating[field] == "pass")
            quality[arm] = all(rating[field] == "pass" for field in _RUBRIC_FIELDS)
            counts[arm]["quality_pass"] += int(quality[arm])
        for field in _RUBRIC_FIELDS:
            baseline_pass = scores["baseline"][field] == "pass"
            copilot_pass = scores["copilot"][field] == "pass"
            if baseline_pass and copilot_pass:
                paired_rubric[field]["both"] += 1
            elif copilot_pass:
                paired_rubric[field]["copilot_only"] += 1
            elif baseline_pass:
                paired_rubric[field]["baseline_only"] += 1
            else:
                paired_rubric[field]["neither"] += 1
        if quality["copilot"] and quality["baseline"]:
            paired["both"] += 1
        elif quality["copilot"]:
            paired["copilot_only"] += 1
        elif quality["baseline"]:
            paired["baseline_only"] += 1
        else:
            paired["neither"] += 1
        if scores["copilot"]["usefulness"] > scores["baseline"]["usefulness"]:
            usefulness["copilot_higher"] += 1
        elif scores["copilot"]["usefulness"] < scores["baseline"]["usefulness"]:
            usefulness["baseline_higher"] += 1
        else:
            usefulness["tie"] += 1
        case_scores.append(
            {
                "case_id": case_id,
                "baseline_quality_pass": quality["baseline"],
                "copilot_quality_pass": quality["copilot"],
                "baseline_usefulness": scores["baseline"]["usefulness"],
                "copilot_usefulness": scores["copilot"]["usefulness"],
            }
        )
    return {
        "schema_version": 1,
        "corpus_id": corpus_id,
        "corpus_sha256": corpus_hash,
        "packet_sha256": packet_hash,
        "review_sha256": content_hash(review),
        "paired_comparison_sha256": packet["paired_comparison_sha256"],
        "reviewer": reviewer,
        "reviewed_at": reviewed_datetime.isoformat(),
        "case_count": len(case_scores),
        "rubric_pass_counts": counts,
        "paired_rubric_outcomes": paired_rubric,
        "paired_quality": paired,
        "usefulness_order": usefulness,
        "cases": case_scores,
        "paired_comparison": paired_comparison,
        "interpretation": (
            "Single-reviewer descriptive task scores joined to hash-bound structural and "
            "runtime summaries. Label masking does not remove stylistic unblinding; latency "
            "is comparable only under matched host/network conditions; token usage is not a "
            "price or billing estimate. No independent efficacy or investment claim follows."
        ),
    }


def _validate_paired_comparison(
    packet: Mapping[str, Any], key: Mapping[str, Any], packet_cases: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate sealed benchmark metrics before joining them to human scores."""
    comparison = key.get("paired_comparison")
    if not isinstance(comparison, Mapping) or content_hash(comparison) != packet.get(
        "paired_comparison_sha256"
    ):
        raise MaskedReviewError("sealed benchmark comparison does not match the packet digest")
    expected_fields = {
        "schema_version",
        "corpus_id",
        "corpus_sha256",
        "snapshot_sha256",
        "baseline_report_sha256",
        "copilot_report_sha256",
        "case_count",
        "categories",
        "baseline_latency_us",
        "copilot_latency_us",
        "copilot_usage",
        "cases",
        "interpretation",
    }
    if set(comparison) != expected_fields or comparison.get("schema_version") != 1:
        raise MaskedReviewError("sealed benchmark comparison schema is invalid")
    for field in (
        "corpus_id",
        "corpus_sha256",
        "snapshot_sha256",
        "baseline_report_sha256",
        "copilot_report_sha256",
    ):
        if comparison.get(field) != packet.get(field):
            raise MaskedReviewError("sealed benchmark comparison identity differs from packet")
    comparison_cases = _case_map(comparison.get("cases"), "paired comparison")
    case_ids = list(packet_cases)
    if list(comparison_cases) != case_ids or comparison.get("case_count") != len(case_ids):
        raise MaskedReviewError("sealed benchmark comparison cases differ from packet")

    categories = {
        "both_pass": 0,
        "baseline_only": 0,
        "copilot_only": 0,
        "neither_or_missing": 0,
    }
    baseline_durations: list[int] = []
    copilot_durations: list[int] = []
    for case_id, case in comparison_cases.items():
        if set(case) != {
            "case_id",
            "baseline_pass",
            "copilot_pass",
            "baseline_duration_us",
            "copilot_duration_us",
            "category",
        }:
            raise MaskedReviewError("sealed benchmark comparison case schema is invalid")
        baseline_pass, copilot_pass = case["baseline_pass"], case["copilot_pass"]
        baseline_duration = case["baseline_duration_us"]
        copilot_duration = case["copilot_duration_us"]
        if (
            case["case_id"] != case_id
            or not isinstance(baseline_pass, bool)
            or not isinstance(copilot_pass, bool)
            or not _valid_duration(baseline_duration)
            or not _valid_duration(copilot_duration)
        ):
            raise MaskedReviewError("sealed benchmark comparison case values are invalid")
        if baseline_pass and copilot_pass:
            category = "both_pass"
        elif baseline_pass:
            category = "baseline_only"
        elif copilot_pass:
            category = "copilot_only"
        else:
            category = "neither_or_missing"
        if case["category"] != category:
            raise MaskedReviewError("sealed benchmark comparison category is inconsistent")
        categories[category] += 1
        baseline_durations.append(baseline_duration)
        copilot_durations.append(copilot_duration)
    if comparison.get("categories") != categories:
        raise MaskedReviewError("sealed benchmark comparison category totals are inconsistent")

    baseline_latency = _latency_summary(baseline_durations)
    copilot_latency = _latency_summary(copilot_durations)
    if (
        comparison.get("baseline_latency_us") != baseline_latency
        or comparison.get("copilot_latency_us") != copilot_latency
    ):
        raise MaskedReviewError("sealed benchmark latency summaries are inconsistent")
    usage = _validated_usage(comparison.get("copilot_usage"))
    if not isinstance(comparison.get("interpretation"), str):
        raise MaskedReviewError("sealed benchmark interpretation is invalid")
    return {
        "case_count": len(case_ids),
        "categories": categories,
        "baseline_latency_us": baseline_latency,
        "copilot_latency_us": copilot_latency,
        "copilot_usage": usage,
        "interpretation": comparison["interpretation"],
    }


def _latency_summary(values: list[int]) -> dict[str, int]:
    ordered = sorted(values)
    return {
        "median": round(median(ordered)),
        "p95_nearest_rank": ordered[math.ceil(len(ordered) * 0.95) - 1],
    }


def _validated_usage(value: object) -> dict[str, int | None] | None:
    if value is None:
        return None
    fields = {"request_count", "duration_ms", "prompt_tokens", "completion_tokens", "total_tokens"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise MaskedReviewError("sealed benchmark usage schema is invalid")
    result: dict[str, int | None] = {}
    for field in fields:
        item = value[field]
        if item is None and field in {"request_count", "duration_ms"}:
            raise MaskedReviewError("sealed benchmark usage is missing required counters")
        if item is not None and (
            not isinstance(item, int) or isinstance(item, bool) or not 0 <= item <= 1_000_000_000
        ):
            raise MaskedReviewError("sealed benchmark usage counter is invalid")
        result[field] = item
    return result


def _valid_duration(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2_000_000_000


def write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Create a private JSON file once, without overwriting prior evidence."""
    ensure_private_output_outside_git(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def read_private_json(path: Path) -> dict[str, Any]:
    try:
        if not hasattr(os, "O_NOFOLLOW"):
            raise MaskedReviewError("platform cannot securely open private review inputs")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            details = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_mode & 0o077
                or (hasattr(os, "geteuid") and details.st_uid != os.geteuid())
            ):
                raise MaskedReviewError("review input must be an owner-only regular file")
            value = orjson.loads(stream.read())
    except MaskedReviewError:
        raise
    except (OSError, orjson.JSONDecodeError) as error:
        raise MaskedReviewError(f"could not read review input: {path.name}") from error
    if not isinstance(value, dict):
        raise MaskedReviewError(f"review input is not an object: {path.name}")
    return value


def _load_verified_answer(
    directory: Path,
    aggregate: Mapping[str, Any],
    case_id: str,
    question: str,
    snapshot_sha256: str,
) -> dict[str, Any]:
    report = read_private_json(directory / f"{case_id}.json")
    verify_copilot_answer_report(report)
    if (
        report.get("evaluation_case_id") != case_id
        or report.get("question") != question
        or report["frozen_identity"]["sha256"] != snapshot_sha256
    ):
        raise MaskedReviewError(f"answer identity mismatch for {case_id}")
    metadata = aggregate.get("benchmark")
    if not isinstance(metadata, Mapping) or report.get("provider") != metadata.get("provider"):
        raise MaskedReviewError(f"answer provider mismatch for {case_id}")
    expected = next(
        (
            item.get("answer_sha256")
            for item in aggregate["cases"]
            if item.get("case_id") == case_id
        ),
        None,
    )
    observed = hashlib.sha256(report["answer"].encode("utf-8")).hexdigest()
    if expected != observed:
        raise MaskedReviewError(f"answer hash mismatch for {case_id}")
    return report


def _visible_answer(report: Mapping[str, Any]) -> dict[str, Any]:
    return {"answer": report["answer"], "citations": report["citations"]}


def _case_map(value: object, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        raise MaskedReviewError(f"{label} cases must contain 1-64 entries")
    result = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("case_id"), str):
            raise MaskedReviewError(f"{label} case identity is invalid")
        case_id = item["case_id"]
        if not _ID.fullmatch(case_id) or case_id in result:
            raise MaskedReviewError(f"{label} case identity is duplicated or invalid")
        result[case_id] = item
    return result


def _parse_rating(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(_RUBRIC_FIELDS) | {"usefulness"}:
        raise MaskedReviewError("rating must contain the exact four gates and usefulness")
    if any(
        not isinstance(value[field], str) or value[field] not in {"pass", "fail"}
        for field in _RUBRIC_FIELDS
    ):
        raise MaskedReviewError("rubric gates must be pass or fail")
    usefulness = value["usefulness"]
    if not isinstance(usefulness, int) or isinstance(usefulness, bool) or not 1 <= usefulness <= 5:
        raise MaskedReviewError("usefulness must be an integer from 1 to 5")
    return dict(value)
