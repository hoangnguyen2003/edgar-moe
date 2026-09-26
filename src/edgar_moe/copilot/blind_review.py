"""Private, label-masked paired human review for copilot versus control.

Randomized A/B order hides explicit provider/model labels, but answer style can
still reveal an arm. This is not a guarantee of perfect reviewer blinding.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import orjson

from edgar_moe.utils.timestamps import parse_aware_timestamp

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
            "cases",
            "review_instruction",
        }
        or set(key) != {"schema_version", "packet_sha256", "assignments"}
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

    counts = {
        arm: {field: 0 for field in _RUBRIC_FIELDS} | {"quality_pass": 0}
        for arm in ("baseline", "copilot")
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
        "reviewer": reviewer,
        "reviewed_at": reviewed_datetime.isoformat(),
        "case_count": len(case_scores),
        "rubric_pass_counts": counts,
        "paired_quality": paired,
        "usefulness_order": usefulness,
        "cases": case_scores,
        "interpretation": (
            "Single-reviewer descriptive task scores only. Label masking does not remove "
            "stylistic unblinding; no independent efficacy or investment claim follows."
        ),
    }


def write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Create a private JSON file once, without overwriting prior evidence."""
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
        value = orjson.loads(path.read_bytes())
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
    if any(value[field] not in {"pass", "fail"} for field in _RUBRIC_FIELDS):
        raise MaskedReviewError("rubric gates must be pass or fail")
    usefulness = value["usefulness"]
    if not isinstance(usefulness, int) or isinstance(usefulness, bool) or not 1 <= usefulness <= 5:
        raise MaskedReviewError("usefulness must be an integer from 1 to 5")
    return dict(value)
