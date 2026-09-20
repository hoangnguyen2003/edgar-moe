from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from edgar_moe.copilot.contracts import COPILOT_DISCLAIMER
from edgar_moe.copilot.policy import COPILOT_POLICY_ID, copilot_policy_sha256
from edgar_moe.copilot.verification import (
    CopilotVerificationError,
    verify_copilot_answer_report,
)


def valid_report() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "question": "What does the frozen study measure?",
        "answer": "The cited snapshot describes the study and its limitations.",
        "model": "gpt-4o-mini",
        "provider": "openai-compatible",
        "created_at": "2026-09-20T05:00:00Z",
        "research_only": True,
        "evidence_status": "grounded",
        "frozen_identity": {
            "path": "data/demo/snapshot.json",
            "sha256": "a" * 64,
            "data_mode": "demo",
            "as_of": "2026-09-18",
            "selection_hash": "b" * 64,
            "locked_test_hash": "c" * 64,
            "research_only": True,
        },
        "citations": [
            {
                "source": "snapshot:data/demo/snapshot.json",
                "label": "Frozen study summary",
                "evidence_sha256": "d" * 64,
                "fields": ["summary"],
            }
        ],
        "tool_trace": [
            {
                "call_index": 1,
                "name": "get_study_summary",
                "arguments_sha256": "e" * 64,
                "result_sha256": "f" * 64,
                "citation_count": 1,
            }
        ],
        "disclaimer": COPILOT_DISCLAIMER,
    }


def test_verifier_accepts_a_grounded_answer_envelope() -> None:
    verify_copilot_answer_report(valid_report())


@pytest.mark.parametrize(
    ("message", "mutate"),
    (
        ("unknown fields", lambda report: report.__setitem__("private_note", "do not retain")),
        ("schema_version", lambda report: report.__setitem__("schema_version", 2)),
        ("research_only", lambda report: report.__setitem__("research_only", False)),
        ("disclaimer", lambda report: report.__setitem__("disclaimer", "trust me")),
        ("question", lambda report: report.__setitem__("question", "")),
        ("model", lambda report: report.__setitem__("model", "https://provider.example/model")),
        ("created_at", lambda report: report.__setitem__("created_at", "not-a-timestamp")),
        ("frozen_identity fields", lambda report: report["frozen_identity"].pop("sha256")),
        (
            "frozen_identity.sha256",
            lambda report: report["frozen_identity"].__setitem__("sha256", "invalid"),
        ),
        (
            "citation 1.source",
            lambda report: report["citations"][0].__setitem__("source", "https://example.test"),
        ),
        (
            "citation 1.evidence_sha256",
            lambda report: report["citations"][0].__setitem__("evidence_sha256", "invalid"),
        ),
        (
            "citation 1.fields",
            lambda report: report["citations"][0].__setitem__("fields", ["ok"] * 65),
        ),
        (
            "tool trace 1 name",
            lambda report: report["tool_trace"][0].__setitem__("name", "bad-name"),
        ),
        (
            "non-read-only tool",
            lambda report: report["tool_trace"][0].__setitem__("name", "execute_trade"),
        ),
        (
            "call indexes",
            lambda report: report["tool_trace"][0].__setitem__("call_index", 2),
        ),
        (
            "citation_count",
            lambda report: report["tool_trace"][0].__setitem__("citation_count", -1),
        ),
        (
            "trace undercounts citations",
            lambda report: report["tool_trace"][0].__setitem__("citation_count", 0),
        ),
        (
            "evidence_status",
            lambda report: report.__setitem__("evidence_status", "unknown"),
        ),
        (
            "grounded copilot answer",
            lambda report: report.__setitem__("citations", []),
        ),
        (
            "uncited copilot answer",
            lambda report: report.__setitem__("evidence_status", "uncited"),
        ),
    ),
)
def test_verifier_rejects_boundary_mutations(message: str, mutate: Any) -> None:
    report = deepcopy(valid_report())
    mutate(report)
    with pytest.raises(CopilotVerificationError, match=message):
        verify_copilot_answer_report(report)


def test_verifier_accepts_benchmark_case_and_api_citation() -> None:
    report = valid_report()
    report["evaluation_case_id"] = "governance-status"
    report["citations"] = [
        {
            "source": "api:/api/v1/governance",
            "label": "Governance contract",
            "evidence_sha256": "1" * 64,
            "fields": [],
        }
    ]

    verify_copilot_answer_report(report)


def test_verifier_accepts_and_checks_agent_identity() -> None:
    report = valid_report()
    report["agent_identity"] = {
        "policy_id": COPILOT_POLICY_ID,
        "policy_sha256": copilot_policy_sha256(),
        "tool_contract_sha256": "1" * 64,
        "max_tool_calls": 4,
        "max_duration_seconds": 300.0,
        "max_context_bytes": 512 * 1024,
    }
    verify_copilot_answer_report(report)

    earlier = deepcopy(report)
    earlier["agent_identity"].pop("max_context_bytes")
    verify_copilot_answer_report(earlier)

    invalid = deepcopy(report)
    invalid["agent_identity"]["policy_id"] = "other-policy"
    with pytest.raises(CopilotVerificationError, match="policy_id"):
        verify_copilot_answer_report(invalid)

    invalid = deepcopy(report)
    invalid["agent_identity"]["policy_sha256"] = "2" * 64
    with pytest.raises(CopilotVerificationError, match="policy_sha256 does not match"):
        verify_copilot_answer_report(invalid)

    invalid = deepcopy(report)
    invalid["agent_identity"]["tool_contract_sha256"] = "invalid"
    with pytest.raises(CopilotVerificationError, match="tool_contract_sha256"):
        verify_copilot_answer_report(invalid)

    invalid = deepcopy(report)
    invalid["agent_identity"]["max_tool_calls"] = 0
    with pytest.raises(CopilotVerificationError, match="max_tool_calls"):
        verify_copilot_answer_report(invalid)

    invalid = deepcopy(report)
    invalid["agent_identity"]["max_duration_seconds"] = 0
    with pytest.raises(CopilotVerificationError, match="max_duration_seconds"):
        verify_copilot_answer_report(invalid)

    invalid = deepcopy(report)
    invalid["agent_identity"]["max_context_bytes"] = 16_383
    with pytest.raises(CopilotVerificationError, match="max_context_bytes"):
        verify_copilot_answer_report(invalid)


def test_verifier_accepts_forward_diagnostic_tool_and_source() -> None:
    report = valid_report()
    report["citations"] = [
        {
            "source": "snapshot:forward-diagnostic",
            "label": "Redacted forward diagnostic",
            "evidence_sha256": "1" * 64,
            "fields": ["coverage", "source_sha256"],
        }
    ]
    report["tool_trace"][0]["name"] = "get_forward_diagnostic"

    verify_copilot_answer_report(report)


def test_verifier_accepts_forward_diagnostic_history_tool_and_source() -> None:
    report = valid_report()
    report["citations"] = [
        {
            "source": "snapshot:forward-diagnostic-history",
            "label": "Verified redacted forward diagnostic history",
            "evidence_sha256": "1" * 64,
            "fields": ["report_count", "history_sha256"],
        }
    ]
    report["tool_trace"][0]["name"] = "get_forward_diagnostic_history"

    verify_copilot_answer_report(report)


def test_verifier_accepts_bounded_usage_and_rejects_unsafe_mutations() -> None:
    report = valid_report()
    report["usage"] = {
        "request_count": 2,
        "duration_ms": 1250,
        "prompt_tokens": 32,
        "completion_tokens": 8,
        "total_tokens": 40,
        "peak_context_bytes": 16_384,
    }
    verify_copilot_answer_report(report)

    invalid = deepcopy(report)
    invalid["usage"]["request_count"] = 0
    with pytest.raises(CopilotVerificationError, match="usage.request_count"):
        verify_copilot_answer_report(invalid)

    invalid = deepcopy(report)
    invalid["usage"]["duration_ms"] = 2_000_001
    with pytest.raises(CopilotVerificationError, match="usage.duration_ms"):
        verify_copilot_answer_report(invalid)

    invalid = deepcopy(report)
    invalid["usage"]["peak_context_bytes"] = 2 * 1024 * 1024 + 1
    with pytest.raises(CopilotVerificationError, match="usage.peak_context_bytes"):
        verify_copilot_answer_report(invalid)

    invalid = deepcopy(report)
    invalid["usage"]["provider_payload"] = "must not be retained"
    with pytest.raises(CopilotVerificationError, match="unknown"):
        verify_copilot_answer_report(invalid)

    bounded = deepcopy(report)
    bounded["usage"]["request_count"] = 36
    verify_copilot_answer_report(bounded)

    invalid = deepcopy(report)
    invalid["usage"]["request_count"] = 37
    with pytest.raises(CopilotVerificationError, match="usage.request_count"):
        verify_copilot_answer_report(invalid)


def test_verifier_rejects_duplicate_citations_and_unknown_trace_fields() -> None:
    duplicate = valid_report()
    duplicate["citations"].append(deepcopy(duplicate["citations"][0]))
    with pytest.raises(CopilotVerificationError, match="duplicate"):
        verify_copilot_answer_report(duplicate)

    unknown_trace = valid_report()
    unknown_trace["tool_trace"][0]["provider_payload"] = "secret"
    with pytest.raises(CopilotVerificationError, match="unknown"):
        verify_copilot_answer_report(unknown_trace)


def test_verifier_rejects_non_object_and_missing_collections() -> None:
    with pytest.raises(CopilotVerificationError, match="JSON object"):
        verify_copilot_answer_report([])  # type: ignore[arg-type]

    report = valid_report()
    report["citations"] = None
    with pytest.raises(CopilotVerificationError, match="citations must be a list"):
        verify_copilot_answer_report(report)

    report = valid_report()
    report["tool_trace"] = None
    with pytest.raises(CopilotVerificationError, match="tool_trace must be a list"):
        verify_copilot_answer_report(report)
