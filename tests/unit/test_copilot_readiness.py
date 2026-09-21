from __future__ import annotations

from copy import deepcopy

import pytest

from edgar_moe.copilot.policy import COPILOT_POLICY_ID, copilot_policy_sha256
from edgar_moe.copilot.readiness import (
    CopilotReadinessError,
    build_copilot_readiness,
    verify_copilot_readiness,
)
from edgar_moe.copilot.review import append_copilot_reviews


def _benchmark(
    *, corpus_id: str = "copilot-v1", answer_sha256: str = "a" * 64
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "corpus_id": corpus_id,
        "corpus_sha256": "c" * 64,
        "case_count": 1,
        "passed_count": 1,
        "pass_rate": 1.0,
        "complete": True,
        "missing_case_ids": [],
        "agent_identity_status": "consistent",
        "agent_identity": {
            "policy_id": COPILOT_POLICY_ID,
            "policy_sha256": copilot_policy_sha256(),
            "tool_contract_sha256": "d" * 64,
            "max_tool_calls": 4,
        },
        "cases": [
            {
                "case_id": "summary",
                "passed": True,
                "failures": [],
                "answer_sha256": answer_sha256,
                "observed_evidence_status": "grounded",
                "observed_tools": ["get_study_summary"],
                "observed_sources": ["snapshot:test"],
                "citation_count": 1,
            }
        ],
        "benchmark": {
            "provider_contacted": True,
            "provider": "test-provider",
            "model": "test-model",
            "selected_case_ids": ["summary"],
            "provider_failures": [],
        },
        "disclaimer": "Structural evaluation only.",
    }


def _review(benchmark: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "corpus_id": benchmark["corpus_id"],
        "corpus_sha256": benchmark["corpus_sha256"],
        "reviews": [
            {
                "case_id": "summary",
                "answer_sha256": benchmark["cases"][0]["answer_sha256"],
                "reviewer": "operator",
                "reviewed_at": "2026-09-20T03:00:00Z",
                "grounding": "pass",
                "citations": "pass",
                "safety": "pass",
                "prose": "pass",
                "decision": "accept",
                "review_codes": [],
            }
        ],
    }


def test_readiness_is_ready_only_for_a_complete_currently_reviewed_benchmark() -> None:
    benchmark = _benchmark()
    history = append_copilot_reviews(benchmark, _review(benchmark), minimum_reviews=1)

    report = build_copilot_readiness(benchmark, history)

    assert report["status"] == "ready"
    assert report["missing_current_case_ids"] == []
    assert report["reviewed_current_case_count"] == 1
    assert "The cited snapshot" not in str(report)
    verify_copilot_readiness(report)


def test_readiness_blocks_an_incomplete_or_failing_benchmark() -> None:
    benchmark = _benchmark()
    history = append_copilot_reviews(benchmark, _review(benchmark), minimum_reviews=1)
    benchmark["complete"] = False
    benchmark["missing_case_ids"] = ["other-case"]
    benchmark["benchmark"]["selected_case_ids"] = ["summary", "other-case"]
    benchmark["pass_rate"] = 0.5
    benchmark["cases"][0]["passed"] = False
    benchmark["cases"][0]["failures"] = ["structural failure"]

    report = build_copilot_readiness(benchmark, history, min_pass_rate=0.9)

    assert report["status"] == "blocked"
    assert report["blocked_reasons"] == [
        "benchmark_complete",
        "benchmark_pass_rate",
        "benchmark_cases_passed",
        "current_case_reviews",
    ]


def test_readiness_requires_reviews_for_the_current_answer_hash() -> None:
    benchmark = _benchmark()
    history = append_copilot_reviews(benchmark, _review(benchmark), minimum_reviews=1)
    changed = deepcopy(benchmark)
    changed["cases"][0]["answer_sha256"] = "b" * 64

    report = build_copilot_readiness(changed, history)

    assert report["status"] == "review_required"
    assert report["missing_current_case_ids"] == ["summary"]


def test_readiness_rejects_tampered_retained_summary() -> None:
    benchmark = _benchmark()
    history = append_copilot_reviews(benchmark, _review(benchmark), minimum_reviews=1)
    report = build_copilot_readiness(benchmark, history)
    report["status"] = "blocked"

    with pytest.raises(CopilotReadinessError, match="content hash"):
        verify_copilot_readiness(report)


def test_readiness_reports_corpus_mismatch_as_blocked() -> None:
    benchmark = _benchmark()
    other_benchmark = _benchmark(corpus_id="other-v1")
    history = append_copilot_reviews(
        other_benchmark,
        _review(other_benchmark),
        minimum_reviews=1,
    )

    report = build_copilot_readiness(benchmark, history)

    assert report["status"] == "blocked"
    assert "corpus_identity" in report["blocked_reasons"]


def test_readiness_requires_a_consistent_agent_identity() -> None:
    benchmark = _benchmark()
    history = append_copilot_reviews(benchmark, _review(benchmark), minimum_reviews=1)

    legacy = deepcopy(benchmark)
    legacy.pop("agent_identity")
    legacy.pop("agent_identity_status")
    legacy_report = build_copilot_readiness(legacy, history)
    assert legacy_report["status"] == "review_required"
    assert legacy_report["agent_identity_status"] == "legacy"
    assert legacy_report["agent_identity"] is None
    assert "agent_identity" in legacy_report["blocked_reasons"]

    mixed = deepcopy(benchmark)
    mixed["agent_identity_status"] = "mixed"
    mixed["agent_identity"] = None
    mixed_report = build_copilot_readiness(mixed, history)
    assert mixed_report["status"] == "review_required"
    assert mixed_report["agent_identity_status"] == "mixed"
    assert "agent_identity" in mixed_report["blocked_reasons"]
