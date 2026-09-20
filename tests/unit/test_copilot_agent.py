from __future__ import annotations

from pathlib import Path

import pytest

from edgar_moe.api.repository import SnapshotRepository
from edgar_moe.copilot.agent import (
    CopilotError,
    CopilotProviderError,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
    ResearchCopilot,
    _parse_provider_response,
    normalize_provider_endpoint,
)
from edgar_moe.copilot.tools import ReadOnlyToolset
from edgar_moe.copilot.verification import CopilotVerificationError


class FakeProvider:
    model = "fake-model"
    provider_name = "test-provider"

    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = responses
        self.messages: list[list[dict[str, object]]] = []

    def complete(
        self,
        messages: list[dict[str, object]],
        tools: tuple[object, ...] | list[object],
    ) -> ProviderResponse:
        self.messages.append(messages)
        return self.responses.pop(0)


def _tool_call(
    name: str, arguments: str = "{}", usage: ProviderUsage | None = None
) -> ProviderResponse:
    return ProviderResponse(
        content="",
        tool_calls=(ProviderToolCall(call_id="call-1", name=name, arguments=arguments),),
        model="fake-model",
        usage=usage,
    )


def test_agent_executes_read_tool_then_returns_citation_backed_answer() -> None:
    provider = FakeProvider(
        [
            _tool_call("get_study_summary"),
            ProviderResponse(
                content=(
                    "The frozen study reports both predictive metrics and cost scenarios; "
                    "review the cited snapshot before drawing conclusions."
                ),
                tool_calls=(),
                model="fake-model",
            ),
        ]
    )
    copilot = ResearchCopilot(
        provider=provider,
        toolset=ReadOnlyToolset(SnapshotRepository(Path("data/demo/snapshot.json"))),
    )

    answer = copilot.ask("What does the frozen study report?")

    assert answer.evidence_status == "grounded"
    assert answer.citations[0].source == "snapshot:data/demo/snapshot.json"
    assert answer.trace[0].name == "get_study_summary"
    assert answer.trace[0].citation_count == 1
    assert any(message.get("role") == "tool" for message in provider.messages[-1])
    assert answer.as_dict()["research_only"] is True


def test_agent_aggregates_bounded_usage_without_retaining_provider_metadata() -> None:
    provider = FakeProvider(
        [
            _tool_call(
                "get_study_summary",
                usage=ProviderUsage(prompt_tokens=12, completion_tokens=3, total_tokens=15),
            ),
            ProviderResponse(
                content="The cited snapshot is the source.",
                tool_calls=(),
                model="fake-model",
                usage=ProviderUsage(prompt_tokens=20, completion_tokens=5, total_tokens=25),
            ),
        ]
    )
    answer = ResearchCopilot(
        provider=provider,
        toolset=ReadOnlyToolset(SnapshotRepository(Path("data/demo/snapshot.json"))),
    ).ask("Summarize the study.")

    assert answer.usage is not None
    assert answer.usage.request_count == 2
    assert answer.usage.duration_ms >= 0
    assert answer.usage.prompt_tokens == 32
    assert answer.usage.completion_tokens == 8
    assert answer.usage.total_tokens == 40
    assert "usage" in answer.as_dict()
    assert "provider_payload" not in answer.as_dict()


def test_agent_rejects_a_tool_loop_that_exceeds_the_budget() -> None:
    provider = FakeProvider([_tool_call("get_methodology"), _tool_call("get_methodology")])
    copilot = ResearchCopilot(
        provider=provider,
        toolset=ReadOnlyToolset(SnapshotRepository(Path("data/demo/snapshot.json"))),
        max_tool_calls=1,
    )

    with pytest.raises(CopilotError, match="budget"):
        copilot.ask("Keep calling tools")


def test_invalid_tool_request_is_returned_without_granting_evidence() -> None:
    provider = FakeProvider(
        [
            _tool_call("execute_trade"),
            ProviderResponse(content="I cannot support that request.", tool_calls=(), model="fake-model"),
        ]
    )
    copilot = ResearchCopilot(
        provider=provider,
        toolset=ReadOnlyToolset(SnapshotRepository(Path("data/demo/snapshot.json"))),
    )

    answer = copilot.ask("Place a trade")

    assert answer.evidence_status == "uncited"
    assert answer.citations == ()
    assert answer.trace[0].citation_count == 0
    assert answer.trace[0].name == "rejected_tool_request"
    assert any(
        '"error": "tool request rejected by the read-only contract"' in str(message)
        for message in provider.messages[-1]
    )


def test_provider_endpoint_rejects_remote_plain_http_and_credentials() -> None:
    assert normalize_provider_endpoint("http://localhost:11434/v1/chat/completions").startswith(
        "http://localhost"
    )
    assert normalize_provider_endpoint("https://api.example.com/v1/chat/completions") == (
        "https://api.example.com/v1/chat/completions"
    )
    with pytest.raises(ValueError):
        normalize_provider_endpoint("http://provider.example/v1/chat/completions")
    with pytest.raises(ValueError):
        normalize_provider_endpoint("https://user:secret@api.example.com/v1/chat/completions")
    with pytest.raises(ValueError):
        normalize_provider_endpoint("https://api.example.com/v1/chat/completions?token=secret")


def test_provider_response_keeps_only_bounded_usage_counters() -> None:
    response = _parse_provider_response(
        {
            "model": "test-model",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
                "provider_secret": "discarded",
            },
        },
        fallback_model="fallback",
    )

    assert response.usage == ProviderUsage(prompt_tokens=4, completion_tokens=2, total_tokens=6)

    with pytest.raises(CopilotProviderError, match="usage was invalid"):
        _parse_provider_response(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"total_tokens": -1},
            },
            fallback_model="fallback",
        )


def test_agent_rejects_an_answer_envelope_with_unsafe_provider_metadata() -> None:
    provider = FakeProvider(
        [ProviderResponse(content="Answer text", tool_calls=(), model="https://provider.example")]
    )
    copilot = ResearchCopilot(
        provider=provider,
        toolset=ReadOnlyToolset(SnapshotRepository(Path("data/demo/snapshot.json"))),
    )

    with pytest.raises(CopilotVerificationError, match="unsafe metadata"):
        copilot.ask("What does the frozen study measure?")
