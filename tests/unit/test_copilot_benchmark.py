from __future__ import annotations

from pathlib import Path

from edgar_moe.copilot.benchmark import run_benchmark, write_benchmark_report
from edgar_moe.copilot.contracts import Citation, CopilotAnswer, ToolTrace
from edgar_moe.copilot.evaluation import EvaluationCase, EvaluationCorpus
from edgar_moe.copilot.verification import CopilotVerificationError


class FakeRunner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def ask(self, question: str) -> CopilotAnswer:
        if self.fail:
            raise RuntimeError("provider response must not be retained")
        return CopilotAnswer(
            question=question,
            answer="The cited snapshot is the source.",
            model="test-model",
            provider="test-provider",
            created_at="2026-01-01T00:00:00+00:00",
            frozen_identity={
                "path": "data/demo/snapshot.json",
                "sha256": "a" * 64,
                "data_mode": "demo",
                "as_of": "2026-01-01",
                "selection_hash": "b" * 64,
                "locked_test_hash": "c" * 64,
                "research_only": True,
            },
            citations=(Citation("snapshot:test", "Test", "b" * 64),),
            trace=(ToolTrace(1, "get_study_summary", "c" * 64, "d" * 64, 1),),
            evidence_status="grounded",
        )


def _corpus() -> EvaluationCorpus:
    return EvaluationCorpus(
        corpus_id="test-corpus",
        cases=(
            EvaluationCase(
                case_id="summary",
                question="Summarize the study.",
                expected_evidence_status="grounded",
                required_tools=("get_study_summary",),
                required_sources=("snapshot:test",),
                min_citations=1,
            ),
        ),
        sha256="e" * 64,
    )


def test_benchmark_writes_private_case_and_safe_aggregate(tmp_path: Path) -> None:
    result = run_benchmark(_corpus(), FakeRunner(), tmp_path)

    assert result.suite.pass_rate == 1.0
    assert not result.failures
    report = tmp_path / "summary.json"
    write_benchmark_report(report, result.as_dict(provider="test-provider", model="test-model"))
    aggregate = report.read_text(encoding="utf-8")
    assert "The cited snapshot" not in aggregate
    assert '"summary"' in aggregate
    assert (tmp_path / "summary.json").stat().st_size > 0


def test_benchmark_records_only_coarse_provider_failure(tmp_path: Path) -> None:
    result = run_benchmark(_corpus(), FakeRunner(fail=True), tmp_path)

    assert result.suite.complete is False
    assert result.failures[0].as_dict() == {"case_id": "summary", "error_type": "RuntimeError"}
    assert list(tmp_path.iterdir()) == []


class InvalidEnvelopeRunner:
    def ask(self, question: str) -> CopilotAnswer:
        return CopilotAnswer(
            question=question,
            answer="This answer must not be written.",
            model="test-model",
            provider="test-provider",
            created_at="2026-01-01T00:00:00+00:00",
            frozen_identity={"sha256": "a" * 64},
            citations=(),
            trace=(),
            evidence_status="uncited",
        )


def test_benchmark_does_not_write_an_unverified_answer_envelope(tmp_path: Path) -> None:
    result = run_benchmark(_corpus(), InvalidEnvelopeRunner(), tmp_path)

    assert result.suite.complete is False
    assert result.failures[0].as_dict() == {
        "case_id": "summary",
        "error_type": CopilotVerificationError.__name__,
    }
    assert list(tmp_path.iterdir()) == []
