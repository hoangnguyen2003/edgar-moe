from __future__ import annotations

from pathlib import Path

import pytest

from edgar_moe.copilot.review import (
    ReviewInputError,
    append_copilot_reviews,
    benchmark_sha256,
    verify_copilot_review_history,
    write_copilot_review_history,
)


def _benchmark() -> dict[str, object]:
    return {
        "schema_version": 1,
        "corpus_id": "copilot-v1",
        "corpus_sha256": "c" * 64,
        "case_count": 1,
        "passed_count": 1,
        "pass_rate": 1.0,
        "complete": True,
        "missing_case_ids": [],
        "cases": [
            {
                "case_id": "summary",
                "passed": True,
                "failures": [],
                "answer_sha256": "a" * 64,
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
        "corpus_id": "copilot-v1",
        "corpus_sha256": "c" * 64,
        "benchmark_sha256": benchmark_sha256(benchmark),
        "reviews": [
            {
                "case_id": "summary",
                "answer_sha256": "a" * 64,
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


def _benchmark_for(case_id: str, answer_sha256: str) -> dict[str, object]:
    benchmark = _benchmark()
    case = dict(benchmark["cases"][0])
    case["case_id"] = case_id
    case["answer_sha256"] = answer_sha256
    benchmark["cases"] = [case]
    metadata = dict(benchmark["benchmark"])
    metadata["selected_case_ids"] = [case_id]
    benchmark["benchmark"] = metadata
    return benchmark


def _review_for(
    benchmark: dict[str, object],
    *,
    reviewed_at: str = "2026-09-20T03:00:00Z",
    decision: str = "accept",
    safety: str = "pass",
    prose: str = "pass",
    review_codes: list[str] | None = None,
) -> dict[str, object]:
    review = _review(benchmark)
    review["corpus_id"] = benchmark["corpus_id"]
    review["corpus_sha256"] = benchmark["corpus_sha256"]
    review.pop("benchmark_sha256", None)
    entry = dict(review["reviews"][0])
    entry["case_id"] = benchmark["cases"][0]["case_id"]
    entry["answer_sha256"] = benchmark["cases"][0]["answer_sha256"]
    entry["reviewed_at"] = reviewed_at
    entry["decision"] = decision
    entry["safety"] = safety
    entry["prose"] = prose
    entry["review_codes"] = review_codes or []
    review["reviews"] = [entry]
    return review


def test_review_history_is_hash_pinned_and_excludes_answer_text(tmp_path: Path) -> None:
    benchmark = _benchmark()
    history = append_copilot_reviews(benchmark, _review(benchmark), minimum_reviews=1)

    verify_copilot_review_history(history)
    assert history["status"] == "accepted"
    assert history["accepted_count"] == 1
    assert history["entry_count"] == 1
    assert "private answer" not in str(history)
    assert "Summarize the study" not in str(history)

    output = tmp_path / "history.json"
    write_copilot_review_history(output, history)
    assert output.exists()


def test_review_history_supports_omitted_benchmark_hash_and_insufficient_status() -> None:
    benchmark = _benchmark()
    review = _review_for(benchmark)
    history = append_copilot_reviews(benchmark, review, minimum_reviews=4)

    assert history["status"] == "insufficient_history"


def test_review_history_records_review_required_and_rejected_statuses() -> None:
    benchmark = _benchmark()
    revise = _review_for(
        benchmark,
        decision="revise",
        prose="fail",
        review_codes=["unclear_prose"],
    )
    assert append_copilot_reviews(benchmark, revise, minimum_reviews=1)["status"] == "review_required"

    reject = _review_for(
        benchmark,
        decision="reject",
        safety="fail",
        review_codes=["unsafe_instruction"],
    )
    assert append_copilot_reviews(benchmark, reject, minimum_reviews=1)["status"] == "rejected"


def test_review_history_rejects_duplicate_answer_review() -> None:
    benchmark = _benchmark()
    review = _review(benchmark)
    history = append_copilot_reviews(benchmark, review, minimum_reviews=1)

    with pytest.raises(ReviewInputError, match="duplicate answer review"):
        append_copilot_reviews(benchmark, review, history, minimum_reviews=1)


def test_review_history_appends_a_chained_entry() -> None:
    first_benchmark = _benchmark()
    first_history = append_copilot_reviews(
        first_benchmark,
        _review_for(first_benchmark),
        minimum_reviews=2,
    )
    second_benchmark = _benchmark_for("governance-status", "b" * 64)
    history = append_copilot_reviews(
        second_benchmark,
        _review_for(second_benchmark, reviewed_at="2026-09-20T04:00:00Z"),
        first_history,
        minimum_reviews=2,
    )

    assert history["status"] == "accepted"
    assert history["entry_count"] == 2
    first_entry, second_entry = history["entries"]
    assert second_entry["sequence"] == 2
    assert second_entry["previous_entry_sha256"] == first_entry["entry_sha256"]
    verify_copilot_review_history(history)


def test_review_history_rejects_invalid_append_context() -> None:
    benchmark = _benchmark()
    history = append_copilot_reviews(benchmark, _review(benchmark), minimum_reviews=1)

    other_corpus = _benchmark()
    other_corpus["corpus_id"] = "other-v1"
    other_history = append_copilot_reviews(
        other_corpus,
        _review_for(other_corpus),
        minimum_reviews=1,
    )
    with pytest.raises(ReviewInputError, match="corpus_id"):
        append_copilot_reviews(benchmark, _review(benchmark), other_history, minimum_reviews=1)

    other_sha = _benchmark()
    other_sha["corpus_sha256"] = "d" * 64
    other_sha_history = append_copilot_reviews(
        other_sha,
        _review_for(other_sha),
        minimum_reviews=1,
    )
    with pytest.raises(ReviewInputError, match="corpus_sha256"):
        append_copilot_reviews(benchmark, _review(benchmark), other_sha_history, minimum_reviews=1)

    with pytest.raises(ReviewInputError, match="minimum_reviews"):
        append_copilot_reviews(benchmark, _review(benchmark), history, minimum_reviews=4)

    second_benchmark = _benchmark_for("governance-status", "b" * 64)
    older_review = _review_for(second_benchmark, reviewed_at="2026-09-19T03:00:00Z")
    with pytest.raises(ReviewInputError, match="chronological"):
        append_copilot_reviews(second_benchmark, older_review, history, minimum_reviews=1)


def test_review_history_rejects_hash_mismatch_and_unsafe_rubric() -> None:
    benchmark = _benchmark()
    review = _review(benchmark)
    review_entry = review["reviews"][0]
    assert isinstance(review_entry, dict)
    review_entry["answer_sha256"] = "b" * 64
    with pytest.raises(ReviewInputError, match="answer hash"):
        append_copilot_reviews(benchmark, review, minimum_reviews=1)

    unsafe_review = _review(benchmark)
    unsafe_entry = unsafe_review["reviews"][0]
    assert isinstance(unsafe_entry, dict)
    unsafe_entry["answer_sha256"] = "a" * 64
    unsafe_entry["safety"] = "fail"
    unsafe_entry["decision"] = "revise"
    unsafe_entry["review_codes"] = ["unsafe_instruction"]
    with pytest.raises(ReviewInputError, match="safety failure"):
        append_copilot_reviews(benchmark, unsafe_review, minimum_reviews=1)


def test_review_history_rejects_invalid_minimum_and_benchmark_schema() -> None:
    benchmark = _benchmark()
    with pytest.raises(ReviewInputError, match="minimum_reviews"):
        append_copilot_reviews(benchmark, _review(benchmark), minimum_reviews=0)

    invalid_benchmark = _benchmark()
    invalid_benchmark["schema_version"] = 2
    with pytest.raises(ReviewInputError, match="schema_version"):
        benchmark_sha256(invalid_benchmark)


def test_review_history_rejects_unsafe_benchmark_metadata() -> None:
    mutations = (
        ("research_only", lambda payload: payload.__setitem__("research_only", True)),
        ("answer text", lambda payload: payload.__setitem__("answer", "private")),
        ("metadata", lambda payload: payload.pop("benchmark")),
        (
            "provider_contacted",
            lambda payload: payload["benchmark"].__setitem__("provider_contacted", False),
        ),
        ("provider", lambda payload: payload["benchmark"].__setitem__("provider", "")),
    )
    for message, mutate in mutations:
        invalid_benchmark = _benchmark()
        mutate(invalid_benchmark)
        with pytest.raises(ReviewInputError, match=message):
            benchmark_sha256(invalid_benchmark)


def test_review_history_detects_tampering() -> None:
    benchmark = _benchmark()
    history = append_copilot_reviews(benchmark, _review(benchmark), minimum_reviews=1)
    history["disclaimer"] = "tampered"

    with pytest.raises(ReviewInputError, match="history hash mismatch"):
        verify_copilot_review_history(history)


def test_review_history_rejects_answer_text_in_aggregate() -> None:
    benchmark = _benchmark()
    benchmark["cases"][0]["answer"] = "private answer"

    with pytest.raises(ReviewInputError, match="answer text"):
        benchmark_sha256(benchmark)
