from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import orjson
import pytest
from typer.testing import CliRunner

from edgar_moe.api.repository import SnapshotRepository
from edgar_moe.cli import app
from edgar_moe.copilot.baseline import DeterministicEvidenceBaseline
from edgar_moe.copilot.benchmark import run_benchmark, select_evaluation_cases
from edgar_moe.copilot.evaluation import EvaluationInputError, load_evaluation_corpus
from edgar_moe.copilot.tools import ReadOnlyToolset
from edgar_moe.copilot.verification import verify_copilot_answer_report
from edgar_moe.settings import runtime_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORPUS = PROJECT_ROOT / "config/copilot_eval_cases.json"


@pytest.fixture
def synthetic_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Use only invented values, at the corpus's logical snapshot path."""
    monkeypatch.chdir(tmp_path)
    path = Path("data/demo/snapshot.json")
    path.parent.mkdir(parents=True)
    path.write_bytes(
        orjson.dumps(
            {
                "metadata": {
                    "data_mode": "synthetic_fixture",
                    "as_of": "2026-01-01",
                    "selection_hash": "a" * 64,
                    "locked_test_hash": "b" * 64,
                    "research_only": True,
                },
                "summary": {"description": "Invented software-test fixture"},
                "predictive_metrics": {},
                "portfolio_scenarios": [],
                "experiments": [],
                "equity_curves": {},
                "events": [],
                "latest_signals": [],
                "methodology": {"target": "Invented software-test target", "limitations": []},
                "freshness": {},
            }
        )
    )
    yield path
    runtime_settings.cache_clear()


def _baseline(snapshot: Path) -> DeterministicEvidenceBaseline:
    repository = SnapshotRepository(snapshot)
    return DeterministicEvidenceBaseline(ReadOnlyToolset(repository))


def test_baseline_passes_existing_corpus_without_provider_calls(
    tmp_path: Path, synthetic_snapshot: Path
) -> None:
    corpus = load_evaluation_corpus(CORPUS)
    result = run_benchmark(corpus, _baseline(synthetic_snapshot), tmp_path / "answers")
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
    assert sorted(path.name for path in (tmp_path / "answers").iterdir()) == sorted(
        f"{case.case_id}.json" for case in corpus.cases
    )


def test_baseline_abstains_when_no_fixed_route_exists(synthetic_snapshot: Path) -> None:
    answer = _baseline(synthetic_snapshot).ask("Compare two undocumented external datasets.")
    verify_copilot_answer_report(answer.as_dict())
    assert answer.frozen_identity["data_mode"] == "synthetic_fixture"
    assert answer.evidence_status == "uncited"
    assert answer.trace == ()
    assert answer.citations == ()


def test_baseline_refuses_order_even_if_question_mentions_metrics(
    synthetic_snapshot: Path,
) -> None:
    answer = _baseline(synthetic_snapshot).ask(
        "Buy the strongest stock using the predictive metrics."
    )
    verify_copilot_answer_report(answer.as_dict())
    assert answer.evidence_status == "uncited"
    assert answer.trace == ()
    assert "cannot" in answer.answer


def test_baseline_routes_by_predeclared_keywords_not_case_id(synthetic_snapshot: Path) -> None:
    answer = _baseline(synthetic_snapshot).ask("What are the limitations of the target and split?")
    verify_copilot_answer_report(answer.as_dict())
    assert answer.evidence_status == "grounded"
    assert answer.trace[0].name == "get_methodology"


def test_both_arms_reject_unknown_and_duplicate_case_selection() -> None:
    corpus = load_evaluation_corpus(CORPUS)
    selected = select_evaluation_cases(corpus, ("frozen-summary",))
    assert [case.case_id for case in selected.cases] == ["frozen-summary"]
    assert selected.sha256 == corpus.sha256
    for ids in (("unknown-case",), ("frozen-summary", "frozen-summary")):
        with pytest.raises(EvaluationInputError):
            select_evaluation_cases(corpus, ids)


def test_baseline_cli_writes_private_aggregate_without_provider(
    tmp_path: Path, synthetic_snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGAR_MOE_DEMO_SNAPSHOT", str(PROJECT_ROOT / "data/demo/snapshot.json"))
    runtime_settings.cache_clear()
    output_dir = tmp_path / "baseline"
    arguments = [
        "research-copilot-baseline",
        "--corpus",
        str(CORPUS),
        "--snapshot",
        str(synthetic_snapshot),
        "--output-dir",
        str(output_dir),
    ]
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 0, result.output
    assert '"provider_contacted": false' in result.output
    report = output_dir / "evaluation.json"
    original = report.read_bytes()
    rerun = CliRunner().invoke(app, arguments)
    assert rerun.exit_code != 0
    assert "not empty" in rerun.output
    assert report.read_bytes() == original
