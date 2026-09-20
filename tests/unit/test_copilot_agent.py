from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from edgar_moe.api.repository import SnapshotRepository
from edgar_moe.copilot.agent import (
    CopilotError,
    CopilotProviderError,
    OpenAICompatibleProvider,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
    ResearchCopilot,
    _parse_provider_response,
    normalize_provider_endpoint,
)
from edgar_moe.copilot.contracts import ToolDefinition, ToolResult
from edgar_moe.copilot.policy import COPILOT_POLICY_ID, copilot_policy_sha256
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


class LargeToolset:
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(
                name="get_large_result",
                description="Return a bounded test payload.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
            ),
        )

    def execute(self, name: str, arguments: dict[str, object]) -> ToolResult:
        del arguments
        return ToolResult(name=name, payload={"blob": "x" * 20_000}, citations=())


def _tool_call(
    name: str,
    arguments: str = "{}",
    usage: ProviderUsage | None = None,
    request_count: int = 1,
) -> ProviderResponse:
    return ProviderResponse(
        content="",
        tool_calls=(ProviderToolCall(call_id="call-1", name=name, arguments=arguments),),
        model="fake-model",
        usage=usage,
        request_count=request_count,
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
    identity = answer.as_dict()["agent_identity"]
    assert identity["policy_id"] == COPILOT_POLICY_ID
    assert identity["policy_sha256"] == copilot_policy_sha256()
    assert len(identity["tool_contract_sha256"]) == 64
    assert identity["max_tool_calls"] == 4
    assert identity["max_duration_seconds"] == 300.0
    assert identity["max_context_bytes"] == 512 * 1024


def test_agent_aggregates_bounded_usage_without_retaining_provider_metadata() -> None:
    provider = FakeProvider(
        [
            _tool_call(
                "get_study_summary",
                usage=ProviderUsage(prompt_tokens=12, completion_tokens=3, total_tokens=15),
                request_count=2,
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
    assert answer.usage.request_count == 3
    assert answer.usage.duration_ms >= 0
    assert answer.usage.prompt_tokens == 32
    assert answer.usage.completion_tokens == 8
    assert answer.usage.total_tokens == 40
    assert answer.usage.peak_context_bytes is not None
    assert answer.usage.peak_context_bytes > 0
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


def test_agent_stops_before_a_provider_call_when_run_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider([_tool_call("get_methodology")])
    clock = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr("edgar_moe.copilot.agent.monotonic", lambda: next(clock))

    copilot = ResearchCopilot(
        provider=provider,
        toolset=ReadOnlyToolset(SnapshotRepository(Path("data/demo/snapshot.json"))),
        max_duration_seconds=1.0,
    )

    with pytest.raises(CopilotError, match="execution time budget"):
        copilot.ask("Keep calling tools")

    assert len(provider.messages) == 1


def test_agent_rejects_invalid_run_duration_budget() -> None:
    provider = FakeProvider([])
    toolset = ReadOnlyToolset(SnapshotRepository(Path("data/demo/snapshot.json")))

    with pytest.raises(ValueError, match="max_duration_seconds"):
        ResearchCopilot(provider=provider, toolset=toolset, max_duration_seconds=0)
    with pytest.raises(ValueError, match="max_duration_seconds"):
        ResearchCopilot(provider=provider, toolset=toolset, max_duration_seconds=901)


def test_agent_rejects_invalid_context_budget() -> None:
    provider = FakeProvider([])
    toolset = ReadOnlyToolset(SnapshotRepository(Path("data/demo/snapshot.json")))

    with pytest.raises(ValueError, match="max_context_bytes"):
        ResearchCopilot(provider=provider, toolset=toolset, max_context_bytes=16_383)
    with pytest.raises(ValueError, match="max_context_bytes"):
        ResearchCopilot(provider=provider, toolset=toolset, max_context_bytes=2_097_153)


def test_agent_stops_before_provider_call_when_context_budget_is_exceeded() -> None:
    provider = FakeProvider(
        [
            _tool_call("get_large_result"),
            ProviderResponse(content="must not be called", tool_calls=(), model="fake-model"),
        ]
    )
    copilot = ResearchCopilot(
        provider=provider,
        toolset=LargeToolset(),  # type: ignore[arg-type]
        max_context_bytes=16_384,
    )

    with pytest.raises(CopilotError, match="context budget"):
        copilot.ask("Read the large result")

    assert len(provider.messages) == 1


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


def test_provider_retries_transient_http_failures_and_counts_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    delays: list[float] = []

    def fake_urlopen(_request: object, *, timeout: float) -> io.BytesIO:
        assert timeout == 3.0
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError("https://provider.example/v1/chat/completions", 503, "busy", {}, None)
        return io.BytesIO(
            json.dumps(
                {"model": "test-model", "choices": [{"message": {"content": "ok"}}]}
            ).encode()
        )

    monkeypatch.setattr("edgar_moe.copilot.agent.urlopen", fake_urlopen)
    monkeypatch.setattr("edgar_moe.copilot.agent.sleep", delays.append)

    response = OpenAICompatibleProvider(
        endpoint="https://provider.example/v1/chat/completions",
        api_key="secret",
        model="test-model",
        timeout_seconds=3.0,
        max_retries=2,
        retry_backoff_seconds=0.5,
    ).complete([], [])

    assert response.content == "ok"
    assert response.request_count == 2
    assert calls == 2
    assert delays == [0.5]


def test_provider_does_not_retry_authentication_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_urlopen(_request: object, *, timeout: float) -> io.BytesIO:
        del timeout
        nonlocal calls
        calls += 1
        raise HTTPError("https://provider.example/v1/chat/completions", 401, "unauthorized", {}, None)

    monkeypatch.setattr("edgar_moe.copilot.agent.urlopen", fake_urlopen)

    with pytest.raises(CopilotProviderError, match="HTTP 401"):
        OpenAICompatibleProvider(
            endpoint="https://provider.example/v1/chat/completions",
            api_key="secret",
            model="test-model",
            max_retries=2,
            retry_backoff_seconds=0,
        ).complete([], [])

    assert calls == 1


def test_provider_retries_transport_failures_and_caps_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_urlopen(_request: object, *, timeout: float) -> io.BytesIO:
        del timeout
        nonlocal calls
        calls += 1
        if calls < 3:
            raise URLError("temporary network failure")
        return io.BytesIO(
            json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        )

    monkeypatch.setattr("edgar_moe.copilot.agent.urlopen", fake_urlopen)
    monkeypatch.setattr("edgar_moe.copilot.agent.sleep", lambda _delay: None)

    response = OpenAICompatibleProvider(
        endpoint="https://provider.example/v1/chat/completions",
        api_key="secret",
        model="test-model",
        max_retries=2,
        retry_backoff_seconds=0,
    ).complete([], [])

    assert response.request_count == 3
    assert calls == 3

    with pytest.raises(ValueError, match="max retries"):
        OpenAICompatibleProvider(
            endpoint="https://provider.example/v1/chat/completions",
            api_key="secret",
            model="test-model",
            max_retries=4,
        )
    with pytest.raises(ValueError, match="retry backoff"):
        OpenAICompatibleProvider(
            endpoint="https://provider.example/v1/chat/completions",
            api_key="secret",
            model="test-model",
            retry_backoff_seconds=6,
        )


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
