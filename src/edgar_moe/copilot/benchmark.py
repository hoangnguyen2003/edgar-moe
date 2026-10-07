"""Bounded operator-run execution for the reviewed copilot evaluation corpus."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Protocol, cast

import orjson

from .contracts import CopilotAnswer
from .evaluation import (
    EvaluationCorpus,
    EvaluationInputError,
    EvaluationSuite,
    answer_report,
    evaluate_reports,
)


class CopilotRunner(Protocol):
    """Minimal runner contract used by the benchmark and its offline tests."""

    def ask(self, question: str) -> CopilotAnswer: ...


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
    case_duration_us: tuple[tuple[str, int], ...] = ()
    snapshot_sha256: str | None = None
    selected_case_ids: tuple[str, ...] = ()

    def as_dict(
        self,
        *,
        provider: str,
        model: str,
        provider_contacted: bool = True,
        comparison_context: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        report = self.suite.as_dict()
        metadata: dict[str, object] = {
            "provider_contacted": provider_contacted,
            "provider": provider,
            "model": model,
            "selected_case_ids": list(self.selected_case_ids)
            if self.selected_case_ids
            else [case.case_id for case in self.suite.cases] + list(self.suite.missing_case_ids),
            "snapshot_sha256": self.snapshot_sha256,
            "case_duration_us": [
                {"case_id": case_id, "duration_us": elapsed}
                for case_id, elapsed in self.case_duration_us
            ],
        }
        metadata["provider_failures" if provider_contacted else "runner_failures"] = [
            failure.as_dict() for failure in self.failures
        ]
        if comparison_context is not None:
            metadata["comparison_context"] = dict(comparison_context)
        report["benchmark"] = metadata
        if self.usage is not None:
            report["usage"] = self.usage.as_dict()
        return report


def run_benchmark(
    corpus: EvaluationCorpus,
    runner: CopilotRunner,
    output_dir: Path,
) -> BenchmarkRun:
    """Run each selected case and write only private individual envelopes."""
    _ensure_private_directory(output_dir)
    if any(output_dir.iterdir()):
        raise FileExistsError(
            "benchmark output directory is not empty; use a fresh private directory"
        )
    reports: list[dict[str, object]] = []
    failures: list[BenchmarkFailure] = []
    durations: list[tuple[str, int]] = []
    for case in corpus.cases:
        started = perf_counter_ns()
        try:
            report = answer_report(runner.ask(case.question), case_id=case.case_id)
            _write_json(output_dir / f"{case.case_id}.json", report)
            reports.append(report)
        except Exception as error:
            failures.append(BenchmarkFailure(case.case_id, type(error).__name__))
        finally:
            durations.append((case.case_id, max(0, (perf_counter_ns() - started) // 1_000)))
    suite = evaluate_reports(tuple(reports), corpus)
    usage = _aggregate_usage(tuple(reports))
    return BenchmarkRun(
        suite=suite,
        failures=tuple(failures),
        usage=usage,
        case_duration_us=tuple(durations),
        snapshot_sha256=_common_snapshot_sha256(tuple(reports)),
        selected_case_ids=tuple(case.case_id for case in corpus.cases),
    )


def select_evaluation_cases(
    corpus: EvaluationCorpus, case_ids: tuple[str, ...]
) -> EvaluationCorpus:
    """Apply the same exact-id selection policy to provider and baseline arms."""
    known_ids = {case.case_id for case in corpus.cases}
    unknown = sorted(set(case_ids) - known_ids)
    if unknown:
        raise EvaluationInputError(f"unknown evaluation case id: {', '.join(unknown)}")
    if len(set(case_ids)) != len(case_ids):
        raise EvaluationInputError("--case values must not contain duplicates")
    return EvaluationCorpus(
        corpus_id=corpus.corpus_id,
        cases=tuple(case for case in corpus.cases if not case_ids or case.case_id in case_ids),
        sha256=corpus.sha256,
    )


def _common_snapshot_sha256(reports: tuple[dict[str, object], ...]) -> str | None:
    digests: set[str] = set()
    for report in reports:
        identity = report.get("frozen_identity")
        if not isinstance(identity, Mapping):
            return None
        digest = identity.get("sha256")
        if not isinstance(digest, str):
            return None
        digests.add(digest)
    return next(iter(digests)) if len(digests) == 1 else None


def write_benchmark_report(path: Path, report: dict[str, object]) -> None:
    """Atomically write the aggregate report without exposing partial JSON."""
    _write_json(path, report)


def ensure_private_output_outside_git(path: Path) -> None:
    """Refuse private answer/review artifacts anywhere inside a Git worktree."""
    resolved = path.resolve()
    for ancestor in (resolved, *resolved.parents):
        if (ancestor / ".git").exists():
            raise PermissionError("private copilot output must be outside a Git working tree")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    ensure_private_output_outside_git(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _ensure_private_directory(path: Path) -> None:
    """Create a fresh owner-only directory or reject an existing broad path."""
    if path.is_symlink():
        raise PermissionError("benchmark output directory must not be a symlink")
    ensure_private_output_outside_git(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise PermissionError("benchmark output directory must have owner-only mode 0700")


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


def _sum_required_counter(usage_records: list[Mapping[str, object]], field: str) -> int | None:
    values = [usage.get(field) for usage in usage_records]
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return None
    return sum(cast(int, value) for value in values)


def _sum_optional_counter(usage_records: list[Mapping[str, object]], field: str) -> int | None:
    values = [usage.get(field) for usage in usage_records]
    if any(value is None for value in values):
        return None
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return None
    return sum(cast(int, value) for value in values)


def _max_optional_counter(usage_records: list[Mapping[str, object]], field: str) -> int | None:
    values = [usage.get(field) for usage in usage_records]
    if any(value is None for value in values):
        return None
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in values
    ):
        return None
    return max(cast(int, value) for value in values)
