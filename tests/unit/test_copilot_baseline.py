from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from edgar_moe.api.repository import SnapshotRepository
from edgar_moe.cli import app
from edgar_moe.copilot.baseline import DeterministicEvidenceBaseline
from edgar_moe.copilot.benchmark import run_benchmark, select_evaluation_cases
from edgar_moe.copilot.evaluation import EvaluationInputError, load_evaluation_corpus
from edgar_moe.copilot.tools import ReadOnlyToolset
from edgar_moe.copilot.verification import verify_copilot_answer_report


def _baseline() -> DeterministicEvidenceBaseline:
    repository = SnapshotRepository(
        "data/demo/snapshot.json", lock_path="config/public_snapshot.lock.json"
    )
    return DeterministicEvidenceBaseline(ReadOnlyToolset(repository))


def test_baseline_passes_existing_corpus_without_provider_calls(tmp_path: Path) -> None:
    corpus = load_evaluation_corpus(Path("config/copilot_eval_cases.json"))
    result = run_benchmark(corpus, _baseline(), tmp_path)
    aggregate = result.as_dict(
        provider="none", model="deterministic-evidence-navigation-v1", provider_contacted=False
    )

    assert result.suite.complete
    assert result.suite.passed_count == len(corpus.cases)
    assert aggregate["benchmark"]["provider_contacted"] is False
    assert aggregate["benchmark"]["runner_failures"] == []
    assert len(aggregate["benchmark"]["case_duration_us"]) == len(corpus.cases)
    assert "answer" not in aggregate
    assert "usage" not in aggregate
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        f"{case.case_id}.json" for case in corpus.cases
    )


def test_baseline_abstains_when_no_fixed_route_exists() -> None:
    answer = _baseline().ask("Compare two undocumented external datasets.")
    verify_copilot_answer_report(answer.as_dict())
    assert answer.evidence_status == "uncited"
    assert answer.trace == ()
    assert answer.citations == ()


def test_baseline_refuses_order_even_if_question_mentions_metrics() -> None:
    answer = _baseline().ask("Buy the strongest stock using the predictive metrics.")
    verify_copilot_answer_report(answer.as_dict())
    assert answer.evidence_status == "uncited"
    assert answer.trace == ()
    assert "cannot" in answer.answer


def test_baseline_routes_by_predeclared_keywords_not_case_id() -> None:
    answer = _baseline().ask("What are the limitations of the target and split?")
    verify_copilot_answer_report(answer.as_dict())
    assert answer.evidence_status == "grounded"
    assert answer.trace[0].name == "get_methodology"


def test_both_arms_reject_unknown_and_duplicate_case_selection() -> None:
    corpus = load_evaluation_corpus(Path("config/copilot_eval_cases.json"))
    selected = select_evaluation_cases(corpus, ("frozen-summary",))
    assert [case.case_id for case in selected.cases] == ["frozen-summary"]
    assert selected.sha256 == corpus.sha256
    for ids in (("unknown-case",), ("frozen-summary", "frozen-summary")):
        with pytest.raises(EvaluationInputError):
            select_evaluation_cases(corpus, ids)


def test_baseline_cli_writes_private_aggregate_without_provider(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["research-copilot-baseline", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert '"provider_contacted": false' in result.output
    report = tmp_path / "evaluation.json"
    original = report.read_bytes()
    rerun = CliRunner().invoke(app, ["research-copilot-baseline", "--output-dir", str(tmp_path)])
    assert rerun.exit_code != 0
    assert "not empty" in rerun.output
    assert report.read_bytes() == original
