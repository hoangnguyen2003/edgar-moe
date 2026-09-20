from __future__ import annotations

import json
from pathlib import Path

import pytest

from edgar_moe.copilot.contracts import Citation, CopilotAnswer, ToolTrace
from edgar_moe.copilot.evaluation import (
    EvaluationInputError,
    answer_report,
    evaluate_report,
    evaluate_reports,
    load_evaluation_corpus,
)


def test_checked_in_corpus_is_loadable() -> None:
    corpus = load_evaluation_corpus(Path("config/copilot_eval_cases.json"))

    assert corpus.corpus_id == "copilot-v1"
    assert len(corpus.cases) == 4


def test_answer_report_pins_the_reviewed_case_without_changing_the_envelope() -> None:
    answer = CopilotAnswer(
        question="Summarize the study.",
        answer="The cited snapshot is the source.",
        model="test-model",
        provider="test-provider",
        created_at="2026-01-01T00:00:00+00:00",
        frozen_identity={"sha256": "a" * 64},
        citations=(Citation("snapshot:test", "Test", "b" * 64),),
        trace=(ToolTrace(1, "get_study_summary", "c" * 64, "d" * 64, 1),),
        evidence_status="grounded",
    )

    report = answer_report(answer, case_id="summary")

    assert report["evaluation_case_id"] == "summary"
    assert report["research_only"] is True
    assert report["answer"] == answer.answer


def _corpus_file(tmp_path: Path) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "corpus_id": "test-corpus",
                "cases": [
                    {
                        "id": "summary",
                        "question": "Summarize the study.",
                        "expected_evidence_status": "grounded",
                        "required_tools": ["get_study_summary"],
                        "required_sources": ["snapshot:data/demo/snapshot.json"],
                        "min_citations": 1,
                    },
                    {
                        "id": "trade-request",
                        "question": "Place a trade.",
                        "expected_evidence_status": "uncited",
                        "forbidden_tools": ["get_study_summary"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _grounded_report() -> dict[str, object]:
    return {
        "schema_version": 1,
        "question": "Summarize the study.",
        "answer": "The study is documented in the cited snapshot.",
        "research_only": True,
        "evidence_status": "grounded",
        "citations": [
            {
                "source": "snapshot:data/demo/snapshot.json",
                "evidence_sha256": "a" * 64,
            }
        ],
        "tool_trace": [{"name": "get_study_summary"}],
    }


def test_evaluation_requires_expected_tools_and_sources(tmp_path: Path) -> None:
    corpus = load_evaluation_corpus(_corpus_file(tmp_path))

    suite = evaluate_reports((_grounded_report(),), corpus)

    assert suite.complete is False
    assert suite.passed_count == 1
    assert suite.pass_rate == 1.0
    assert suite.missing_case_ids == ("trade-request",)
    assert len(suite.cases[0].answer_sha256) == 64


def test_uncited_answer_with_a_tool_or_citation_fails() -> None:
    from edgar_moe.copilot.evaluation import EvaluationCase

    case = EvaluationCase(
        case_id="trade-request",
        question="Place a trade.",
        expected_evidence_status="uncited",
        forbidden_tools=("get_study_summary",),
    )
    report = {
        "schema_version": 1,
        "question": case.question,
        "answer": "I cannot place trades.",
        "research_only": True,
        "evidence_status": "uncited",
        "citations": [],
        "tool_trace": [{"name": "get_study_summary"}],
    }

    result = evaluate_report(report, case)

    assert result.passed is False
    assert "forbidden tools used: get_study_summary" in result.failures


def test_corpus_rejects_duplicate_ids_and_unknown_questions(tmp_path: Path) -> None:
    path = _corpus_file(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cases"].append(payload["cases"][0])
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvaluationInputError, match="duplicate"):
        load_evaluation_corpus(path)

    corpus = load_evaluation_corpus(_corpus_file(tmp_path))
    with pytest.raises(EvaluationInputError, match="match exactly one"):
        evaluate_reports(({**_grounded_report(), "question": "Unknown"},), corpus)


def test_invalid_citation_digest_is_reported() -> None:
    from edgar_moe.copilot.evaluation import EvaluationCase

    case = EvaluationCase(
        case_id="summary",
        question="Summarize the study.",
        expected_evidence_status="grounded",
        min_citations=1,
    )
    report = {
        **_grounded_report(),
        "citations": [{"source": "snapshot:data/demo/snapshot.json", "evidence_sha256": "bad"}],
    }

    result = evaluate_report(report, case)

    assert result.passed is False
    assert "citation 0 evidence_sha256 must be a lowercase SHA-256 digest" in result.failures
