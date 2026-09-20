"""Bounded operator-run execution for the reviewed copilot evaluation corpus."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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
class BenchmarkRun:
    """Answer reports, structural score, and safe failure metadata."""

    suite: EvaluationSuite
    failures: tuple[BenchmarkFailure, ...]

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
    return BenchmarkRun(suite=suite, failures=tuple(failures))


def write_benchmark_report(path: Path, report: dict[str, object]) -> None:
    """Atomically write the aggregate report without exposing partial JSON."""
    _write_json(path, report)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    temporary.replace(path)
