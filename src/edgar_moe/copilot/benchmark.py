"""Bounded operator-run execution for the reviewed copilot evaluation corpus."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import orjson

from .contracts import CopilotAnswer
from .evaluation import EvaluationCorpus, EvaluationSuite, answer_report, evaluate_reports


class CopilotRunner(Protocol):
    """Minimal runner contract used by the benchmark and its offline tests."""

    def ask(self, question: str) -> CopilotAnswer:
        ...


@dataclass(frozen=True)
class BenchmarkFailure:
    """Coarse, non-sensitive record for one provider failure."""

    case_id: str
    error_type: str

    def as_dict(self) -> dict[str, str]:
        return {"case_id": self.case_id, "error_type": self.error_type}


@dataclass(frozen=True)
class BenchmarkUsage:
    """Safe aggregate telemetry for successful benchmark answers."""

    answer_count: int
    request_count: int
    duration_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    peak_context_bytes: int | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "answer_count": self.answer_count,
            "request_count": self.request_count,
            "duration_ms": self.duration_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }
        if self.peak_context_bytes is not None:
            payload["peak_context_bytes"] = self.peak_context_bytes
        return payload


@dataclass(frozen=True)
class BenchmarkRun:
    """Answer reports, structural score, safe failure metadata, and telemetry."""

    suite: EvaluationSuite
    failures: tuple[BenchmarkFailure, ...]
    usage: BenchmarkUsage | None = None

    def as_dict(self, *, provider: str, model: str) -> dict[str, object]:
        report = self.suite.as_dict()
        report["benchmark"] = {
            "provider_contacted": True,
            "provider": provider,
            "model": model,
            "selected_case_ids": [case.case_id for case in self.suite.cases]
            + list(self.suite.missing_case_ids),
            "provider_failures": [failure.as_dict() for failure in self.failures],
        }
        if self.usage is not None:
            report["usage"] = self.usage.as_dict()
        return report


def run_benchmark(
    corpus: EvaluationCorpus,
    runner: CopilotRunner,
    output_dir: Path,
) -> BenchmarkRun:
    """Run each selected case and write only private individual envelopes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, object]] = []
    failures: list[BenchmarkFailure] = []
    for case in corpus.cases:
        try:
            report = answer_report(runner.ask(case.question), case_id=case.case_id)
            _write_json(output_dir / f"{case.case_id}.json", report)
            reports.append(report)
        except Exception as error:
            failures.append(BenchmarkFailure(case.case_id, type(error).__name__))
    suite = evaluate_reports(tuple(reports), corpus)
    usage = _aggregate_usage(tuple(reports))
    return BenchmarkRun(suite=suite, failures=tuple(failures), usage=usage)


def write_benchmark_report(path: Path, report: dict[str, object]) -> None:
    """Atomically write the aggregate report without exposing partial JSON."""
    _write_json(path, report)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    temporary.replace(path)


def _aggregate_usage(reports: tuple[dict[str, object], ...]) -> BenchmarkUsage | None:
    """Aggregate only verified usage fields, omitting legacy reports without usage."""
    if not reports:
        return None
    usage_records: list[Mapping[str, object]] = []
    for report in reports:
        usage = report.get("usage")
        if not isinstance(usage, Mapping):
            return None
        usage_records.append(usage)

    request_count = _sum_required_counter(usage_records, "request_count")
    duration_ms = _sum_required_counter(usage_records, "duration_ms")
    if request_count is None or duration_ms is None:
        return None
    return BenchmarkUsage(
        answer_count=len(reports),
        request_count=request_count,
        duration_ms=duration_ms,
        prompt_tokens=_sum_optional_counter(usage_records, "prompt_tokens"),
        completion_tokens=_sum_optional_counter(usage_records, "completion_tokens"),
        total_tokens=_sum_optional_counter(usage_records, "total_tokens"),
        peak_context_bytes=_max_optional_counter(usage_records, "peak_context_bytes"),
    )


def _sum_required_counter(
    usage_records: list[Mapping[str, object]], field: str
) -> int | None:
    values = [usage.get(field) for usage in usage_records]
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return None
    return sum(cast(int, value) for value in values)


def _sum_optional_counter(
    usage_records: list[Mapping[str, object]], field: str
) -> int | None:
    values = [usage.get(field) for usage in usage_records]
    if any(value is None for value in values):
        return None
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return None
    return sum(cast(int, value) for value in values)


def _max_optional_counter(
    usage_records: list[Mapping[str, object]], field: str
) -> int | None:
    values = [usage.get(field) for usage in usage_records]
    if any(value is None for value in values):
        return None
    if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in values):
        return None
    return max(cast(int, value) for value in values)
