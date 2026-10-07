"""Synthetic report presentation, not provider execution or factuality scoring."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from edgar_moe.copilot.contracts import Citation, CopilotAnswer, ToolTrace, content_hash
from edgar_moe.copilot.navigation import NAVIGATOR_VERSION
from edgar_moe.copilot.presentation import render_copilot_answer_text
from edgar_moe.copilot.verification import CopilotVerificationError


@pytest.fixture
def report() -> dict[str, Any]:
    return CopilotAnswer(
        question="Describe invented research metrics",
        answer="Synthetic demonstration only.\n[1] Invented RMSE=0.07, not tradability evidence.",
        model=NAVIGATOR_VERSION,
        provider="none",
        created_at="2026-01-02T00:00:00+00:00",
        frozen_identity={
            "path": "data/demo/snapshot.json",
            "sha256": "a" * 64,
            "data_mode": "synthetic_fixture",
            "as_of": "2026-01-02",
            "research_only": True,
            "selection_hash": None,
            "locked_test_hash": None,
        },
        citations=(
            Citation(
                "snapshot:data/demo/snapshot.json",
                "Invented metrics",
                "b" * 64,
                ("predictive_metrics",),
            ),
        ),
        trace=(ToolTrace(1, "get_study_summary", content_hash({}), "b" * 64, 1),),
        evidence_status="grounded",
    ).as_dict()


def test_text_keeps_answer_sources_and_research_limits_without_mutating_report(
    report: dict[str, Any],
) -> None:
    before = deepcopy(report)
    text = render_copilot_answer_text(report)
    assert report == before
    assert report["answer"] in text
    assert "Research-only evidence navigator" in text
    assert NAVIGATOR_VERSION in text
    assert "Evidence: grounded (citation status, not a factuality score)" in text
    assert "[1] Invented metrics" in text
    assert "Source: snapshot:data/demo/snapshot.json" in text
    assert "Evidence SHA-256: " + "b" * 64 in text
    assert "Snapshot SHA-256: " + "a" * 64 in text
    assert "Fields: predictive_metrics" in text
    assert report["disclaimer"] in text
    assert "not licensing approval or proof of performance" in text
    assert report["question"] not in text


def test_refusal_does_not_gain_sources_or_grounded_status(report: dict[str, Any]) -> None:
    report.update(
        {
            "answer": "I cannot place an order.",
            "evidence_status": "uncited",
            "citations": [],
            "tool_trace": [],
        }
    )
    text = render_copilot_answer_text(report)
    assert report["answer"] in text
    assert "Evidence: uncited" in text
    assert "None; this answer does not cite source evidence." in text
    assert "Evidence SHA-256:" not in text


def test_model_presentation_does_not_remap_prose_citations_or_claim_factuality(
    report: dict[str, Any],
) -> None:
    report.update({"provider": "synthetic-test-provider", "model": "invented-model"})
    text = render_copilot_answer_text(report)
    assert "Experimental model output; citations do not establish factual accuracy." in text
    assert "it does not remap model prose" in text


def test_empty_source_field_list_is_explicit(report: dict[str, Any]) -> None:
    report["citations"][0]["fields"] = []
    assert "Fields: not specified" in render_copilot_answer_text(report)


@pytest.mark.parametrize(
    "control,escaped",
    [
        ("\x1b", "\\u001b"),
        ("\r", "\\u000d"),
        ("\b", "\\u0008"),
        ("\x9b", "\\u009b"),
        ("\u202e", "\\u202e"),
        ("\u200b", "\\u200b"),
        ("\u2028", "\\u2028"),
        ("\U000e0001", "\\U000e0001"),
    ],
)
def test_terminal_controls_are_visible_data_and_do_not_change_saved_text(
    report: dict[str, Any], control: str, escaped: str
) -> None:
    report["answer"] = "First line\n" + control + "[31mInvented text\tkept"
    before = deepcopy(report)
    text = render_copilot_answer_text(report)
    assert report == before
    assert control not in text
    assert escaped in text
    assert "First line\n" in text
    assert "text\tkept" in text


def test_inline_source_metadata_cannot_add_fake_terminal_lines(report: dict[str, Any]) -> None:
    report["citations"][0].update(
        {
            "label": "Invented label\nFake heading",
            "fields": ["metric\tname\r\nFake field"],
            "source": "snapshot:demo\x1b[31m",
        }
    )
    text = render_copilot_answer_text(report)
    assert "[1] Invented label\\u000aFake heading" in text
    assert "metric\\u0009name\\u000d\\u000aFake field" in text
    assert "snapshot:demo\\u001b[31m" in text


@pytest.mark.parametrize(
    "field,value",
    [
        ("research_only", False),
        ("evidence_status", "approved"),
        ("citations", []),
        ("tool_trace", []),
        ("schema_version", 9),
    ],
)
def test_invalid_envelope_never_becomes_a_readable_valid_looking_answer(
    report: dict[str, Any], field: str, value: object
) -> None:
    report[field] = value
    with pytest.raises(CopilotVerificationError):
        render_copilot_answer_text(report)
