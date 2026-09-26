from __future__ import annotations

from pathlib import Path

import pytest

from edgar_moe.copilot.benchmark import run_benchmark, write_benchmark_report
from edgar_moe.copilot.contracts import (
    Citation,
    CopilotAgentIdentity,
    CopilotAnswer,
    CopilotUsage,
    ToolTrace,
)
from edgar_moe.copilot.evaluation import EvaluationCase, EvaluationCorpus
from edgar_moe.copilot.policy import COPILOT_POLICY_ID, copilot_policy_sha256
from edgar_moe.copilot.verification import CopilotVerificationError


class FakeRunner:
    def __init__(
        self,
        *,
        fail: bool = False,
        usage: CopilotUsage | None = None,
        agent_identity: CopilotAgentIdentity | None = None,
    ) -> None:
        self.fail = fail
        self.usage = usage
        self.agent_identity = agent_identity

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
            usage=self.usage,
            agent_identity=self.agent_identity,
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
    result = run_benchmark(
        _corpus(),
        FakeRunner(
            usage=CopilotUsage(
                request_count=2,
                duration_ms=125,
                prompt_tokens=10,
                completion_tokens=4,
                total_tokens=14,
                peak_context_bytes=16_384,
            )
        ),
        tmp_path,
    )

    assert result.suite.pass_rate == 1.0
    assert not result.failures
    report = tmp_path / "summary.json"
    write_benchmark_report(report, result.as_dict(provider="test-provider", model="test-model"))
    aggregate = report.read_text(encoding="utf-8")
    assert "The cited snapshot" not in aggregate
    assert '"answer_count": 1' in aggregate
    assert '"request_count": 2' in aggregate
    assert '"duration_ms": 125' in aggregate
    assert '"prompt_tokens": 10' in aggregate
    assert '"completion_tokens": 4' in aggregate
    assert '"total_tokens": 14' in aggregate
    assert '"peak_context_bytes": 16384' in aggregate
    assert '"summary"' in aggregate
    assert (tmp_path / "summary.json").stat().st_size > 0


def test_benchmark_keeps_unreported_token_counters_null(tmp_path: Path) -> None:
    result = run_benchmark(
        _corpus(),
        FakeRunner(
            usage=CopilotUsage(
                request_count=1,
                duration_ms=75,
                prompt_tokens=None,
                completion_tokens=3,
                total_tokens=None,
            )
        ),
        tmp_path,
    )

    aggregate = result.as_dict(provider="test-provider", model="test-model")

    assert aggregate["usage"] == {
        "answer_count": 1,
        "request_count": 1,
        "duration_ms": 75,
        "prompt_tokens": None,
        "completion_tokens": 3,
        "total_tokens": None,
    }


def test_benchmark_omits_aggregate_for_legacy_answers_without_usage(tmp_path: Path) -> None:
    result = run_benchmark(_corpus(), FakeRunner(), tmp_path)

    assert "usage" not in result.as_dict(provider="test-provider", model="test-model")


def test_benchmark_retains_a_consistent_agent_identity(tmp_path: Path) -> None:
    identity = CopilotAgentIdentity(
        policy_id=COPILOT_POLICY_ID,
        policy_sha256=copilot_policy_sha256(),
        tool_contract_sha256="d" * 64,
        max_tool_calls=4,
    )
    result = run_benchmark(_corpus(), FakeRunner(agent_identity=identity), tmp_path)

    aggregate = result.as_dict(provider="test-provider", model="test-model")

    assert aggregate["agent_identity_status"] == "consistent"
    assert aggregate["agent_identity"] == identity.as_dict()


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


def test_benchmark_refuses_to_overwrite_a_private_prior_run(tmp_path: Path) -> None:
    existing = tmp_path / "summary.json"
    existing.write_text("private prior answer", encoding="utf-8")

    class ShouldNotRun:
        def ask(self, question: str) -> CopilotAnswer:
            raise AssertionError("runner must not be called before the output preflight")

    with pytest.raises(FileExistsError, match="not empty"):
        run_benchmark(_corpus(), ShouldNotRun(), tmp_path)
    assert existing.read_text(encoding="utf-8") == "private prior answer"


def test_selected_case_order_survives_a_middle_provider_failure(tmp_path: Path) -> None:
    corpus = EvaluationCorpus(
        corpus_id="ordered-corpus",
        cases=tuple(
            EvaluationCase(
                case_id=case_id,
                question=case_id,
                expected_evidence_status="grounded",
                required_tools=("get_study_summary",),
                required_sources=("snapshot:test",),
                min_citations=1,
            )
            for case_id in ("first", "middle", "last")
        ),
        sha256="e" * 64,
    )

    class FailMiddle(FakeRunner):
        def ask(self, question: str) -> CopilotAnswer:
            if question == "middle":
                raise RuntimeError("private provider failure")
            return super().ask(question)

    aggregate = run_benchmark(corpus, FailMiddle(), tmp_path).as_dict(
        provider="test-provider", model="test-model"
    )
    assert aggregate["benchmark"]["selected_case_ids"] == ["first", "middle", "last"]
    assert aggregate["missing_case_ids"] == ["middle"]
