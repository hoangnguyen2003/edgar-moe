"""Offline structural comparison of copilot and deterministic-baseline runs.

This comparison intentionally cannot conclude that generated prose is more
useful. A blinded human task review on a held-out corpus is still required.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from statistics import median
from typing import Any

from .contracts import content_hash

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_MAX_CASES = 64
_CONTEXT_FLAGS = (
    "registry_configured",
    "diagnostic_configured",
    "diagnostic_history_configured",
)


class PairedBenchmarkError(ValueError):
    """A benchmark pair is incomplete, incompatible, or unsafe to compare."""


def compare_benchmarks(
    baseline: Mapping[str, Any], copilot: Mapping[str, Any]
) -> dict[str, object]:
    """Compare matched structural outcomes without copying private answer text."""
    left = _validated_arm(baseline, provider_contacted=False)
    right = _validated_arm(copilot, provider_contacted=True)
    if (
        left["corpus_id"] != right["corpus_id"]
        or left["corpus_sha256"] != right["corpus_sha256"]
        or left["snapshot_sha256"] != right["snapshot_sha256"]
        or left["selected_case_ids"] != right["selected_case_ids"]
        or left["comparison_context"] != right["comparison_context"]
    ):
        raise PairedBenchmarkError("arms must share corpus, snapshot, cases, and tool context")
    cases = []
    categories = {"both_pass": 0, "baseline_only": 0, "copilot_only": 0, "neither_or_missing": 0}
    for case_id in left["selected_case_ids"]:
        baseline_pass = left["passes"].get(case_id)
        copilot_pass = right["passes"].get(case_id)
        if baseline_pass is True and copilot_pass is True:
            category = "both_pass"
        elif baseline_pass is True:
            category = "baseline_only"
        elif copilot_pass is True:
            category = "copilot_only"
        else:
            category = "neither_or_missing"
        categories[category] += 1
        cases.append(
            {
                "case_id": case_id,
                "baseline_pass": baseline_pass,
                "copilot_pass": copilot_pass,
                "baseline_duration_us": left["durations"][case_id],
                "copilot_duration_us": right["durations"][case_id],
                "category": category,
            }
        )
    return {
        "schema_version": 1,
        "corpus_id": left["corpus_id"],
        "corpus_sha256": left["corpus_sha256"],
        "snapshot_sha256": left["snapshot_sha256"],
        "baseline_report_sha256": content_hash(baseline),
        "copilot_report_sha256": content_hash(copilot),
        "case_count": len(cases),
        "categories": categories,
        "baseline_latency_us": _latency_summary(left["durations"].values()),
        "copilot_latency_us": _latency_summary(right["durations"].values()),
        "copilot_usage": _safe_usage(copilot.get("usage")),
        "cases": cases,
        "interpretation": (
            "Structural evidence-contract comparison only. Passing does not establish factual "
            "correctness or incremental LLM usefulness; use blinded held-out human task review. "
            "Latency is comparable only when both arms run under matched host/network conditions. "
            "Token counts are not a price or billing estimate. Input aggregates are not "
            "cryptographically signed or independently authenticated."
        ),
    }


def _validated_arm(report: Mapping[str, Any], *, provider_contacted: bool) -> dict[str, Any]:
    if not isinstance(report, Mapping) or report.get("schema_version") != 1:
        raise PairedBenchmarkError("benchmark aggregate schema is invalid")
    if _contains_private_text_field(report):
        raise PairedBenchmarkError("benchmark aggregate must not contain answer text or questions")
    metadata = report.get("benchmark")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("provider_contacted") is not provider_contacted
    ):
        raise PairedBenchmarkError("benchmark arm has the wrong provider-contact status")
    corpus_id = report.get("corpus_id")
    corpus_sha256 = report.get("corpus_sha256")
    snapshot_sha256 = metadata.get("snapshot_sha256")
    comparison_context = metadata.get("comparison_context")
    if (
        not isinstance(corpus_id, str)
        or not _CASE_ID.fullmatch(corpus_id)
        or not isinstance(corpus_sha256, str)
        or not _SHA256.fullmatch(corpus_sha256)
        or not isinstance(snapshot_sha256, str)
        or not _SHA256.fullmatch(snapshot_sha256)
    ):
        raise PairedBenchmarkError("benchmark corpus or snapshot identity is invalid")
    if (
        not isinstance(comparison_context, Mapping)
        or set(comparison_context) != set(_CONTEXT_FLAGS) | {"tool_contract_sha256"}
        or any(not isinstance(comparison_context.get(flag), bool) for flag in _CONTEXT_FLAGS)
        or not isinstance(comparison_context.get("tool_contract_sha256"), str)
        or not _SHA256.fullmatch(comparison_context["tool_contract_sha256"])
    ):
        raise PairedBenchmarkError("benchmark tool context is missing or invalid")
    selected = metadata.get("selected_case_ids")
    if (
        not isinstance(selected, list)
        or not 1 <= len(selected) <= _MAX_CASES
        or any(
            not isinstance(case_id, str) or not _CASE_ID.fullmatch(case_id) for case_id in selected
        )
        or len(set(selected)) != len(selected)
    ):
        raise PairedBenchmarkError("benchmark selected case IDs are invalid")
    raw_cases = report.get("cases")
    missing = report.get("missing_case_ids")
    if not isinstance(raw_cases, list) or not isinstance(missing, list):
        raise PairedBenchmarkError("benchmark cases or missing IDs are invalid")
    if any(
        not isinstance(case_id, str) or not _CASE_ID.fullmatch(case_id) for case_id in missing
    ) or len(set(missing)) != len(missing):
        raise PairedBenchmarkError("benchmark missing case IDs are invalid")
    passes: dict[str, bool] = {}
    for case in raw_cases:
        if not isinstance(case, Mapping) or "answer" in case or "question" in case:
            raise PairedBenchmarkError("benchmark case contains an invalid or private field")
        case_id = case.get("case_id")
        passed = case.get("passed")
        if (
            not isinstance(case_id, str)
            or not _CASE_ID.fullmatch(case_id)
            or not isinstance(passed, bool)
            or case_id in passes
        ):
            raise PairedBenchmarkError("benchmark case identity or pass status is invalid")
        passes[case_id] = passed
    if set(selected) != set(passes) | set(missing) or set(passes) & set(missing):
        raise PairedBenchmarkError("benchmark selected, completed, and missing IDs disagree")
    raw_durations = metadata.get("case_duration_us")
    if not isinstance(raw_durations, list):
        raise PairedBenchmarkError("benchmark lacks per-case local duration measurements")
    durations: dict[str, int] = {}
    for item in raw_durations:
        if not isinstance(item, Mapping) or set(item) != {"case_id", "duration_us"}:
            raise PairedBenchmarkError("benchmark case duration schema is invalid")
        case_id = item["case_id"]
        duration = item["duration_us"]
        if (
            not isinstance(case_id, str)
            or not _CASE_ID.fullmatch(case_id)
            or case_id in durations
            or not isinstance(duration, int)
            or isinstance(duration, bool)
            or not 0 <= duration <= 2_000_000_000
        ):
            raise PairedBenchmarkError("benchmark case duration is invalid")
        durations[case_id] = duration
    if set(durations) != set(selected):
        raise PairedBenchmarkError("benchmark durations do not cover selected cases")
    return {
        "corpus_id": corpus_id,
        "corpus_sha256": corpus_sha256,
        "snapshot_sha256": snapshot_sha256,
        "comparison_context": dict(comparison_context),
        "selected_case_ids": selected,
        "passes": passes,
        "durations": durations,
    }


def _latency_summary(values: Iterable[int]) -> dict[str, int]:
    ordered = sorted(values)
    return {
        "median": round(median(ordered)),
        "p95_nearest_rank": ordered[math.ceil(len(ordered) * 0.95) - 1],
    }


def _safe_usage(value: object) -> dict[str, int | None] | None:
    if not isinstance(value, Mapping):
        return None
    fields = ("request_count", "duration_ms", "prompt_tokens", "completion_tokens", "total_tokens")
    result: dict[str, int | None] = {}
    for field in fields:
        item = value.get(field)
        if field in {"request_count", "duration_ms"} and item is None:
            raise PairedBenchmarkError("copilot usage is missing required counters")
        if item is not None and (
            not isinstance(item, int) or isinstance(item, bool) or not 0 <= item <= 1_000_000_000
        ):
            raise PairedBenchmarkError("copilot usage counters are invalid")
        result[field] = item
    return result


def _contains_private_text_field(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in {"answer", "question"} or _contains_private_text_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_private_text_field(item) for item in value)
    return False
