"""Contracts shared by the read-only research copilot and its provider adapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal


def content_hash(value: object) -> str:
    """Return a stable hash for a JSON-serializable evidence payload."""
    encoded = json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Citation:
    """A pointer to immutable or read-only evidence used by an answer."""

    source: str
    label: str
    evidence_sha256: str
    fields: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "label": self.label,
            "evidence_sha256": self.evidence_sha256,
            "fields": list(self.fields),
        }


@dataclass(frozen=True)
class ToolDefinition:
    """OpenAI-compatible function declaration for one allowed read-only tool."""

    name: str
    description: str
    parameters: dict[str, object]

    def as_provider_schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    """Sanitized tool output sent to the model and retained in the trace."""

    name: str
    payload: object
    citations: tuple[Citation, ...]

    def as_provider_content(self) -> str:
        return json.dumps(
            {
                "tool": self.name,
                "data": self.payload,
                "citations": [citation.as_dict() for citation in self.citations],
            },
            default=str,
            ensure_ascii=False,
            sort_keys=True,
        )


@dataclass(frozen=True)
class ToolTrace:
    """A non-sensitive audit record for one model-requested tool call."""

    call_index: int
    name: str
    arguments_sha256: str
    result_sha256: str
    citation_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "call_index": self.call_index,
            "name": self.name,
            "arguments_sha256": self.arguments_sha256,
            "result_sha256": self.result_sha256,
            "citation_count": self.citation_count,
        }


@dataclass(frozen=True)
class CopilotAgentIdentity:
    """Content-addressed identity of the policy and tool surface used by an agent."""

    policy_id: str
    policy_sha256: str
    tool_contract_sha256: str
    max_tool_calls: int

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "policy_sha256": self.policy_sha256,
            "tool_contract_sha256": self.tool_contract_sha256,
            "max_tool_calls": self.max_tool_calls,
        }


@dataclass(frozen=True)
class CopilotUsage:
    """Bounded, non-sensitive execution telemetry for one copilot answer."""

    request_count: int
    duration_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "request_count": self.request_count,
            "duration_ms": self.duration_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


EvidenceStatus = Literal["grounded", "uncited"]

COPILOT_DISCLAIMER = (
    "Evidence-grounded research assistance only; this output is not investment advice, "
    "a forecast, a suitability assessment, or an order instruction."
)


@dataclass(frozen=True)
class CopilotAnswer:
    """Auditable answer envelope; it is never a forecast or an order."""

    question: str
    answer: str
    model: str
    provider: str
    created_at: str
    frozen_identity: dict[str, Any]
    citations: tuple[Citation, ...]
    trace: tuple[ToolTrace, ...]
    evidence_status: EvidenceStatus
    usage: CopilotUsage | None = None
    agent_identity: CopilotAgentIdentity | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "question": self.question,
            "answer": self.answer,
            "model": self.model,
            "provider": self.provider,
            "created_at": self.created_at,
            "research_only": True,
            "evidence_status": self.evidence_status,
            "frozen_identity": self.frozen_identity,
            "citations": [citation.as_dict() for citation in self.citations],
            "tool_trace": [item.as_dict() for item in self.trace],
            "disclaimer": COPILOT_DISCLAIMER,
        }
        if self.usage is not None:
            payload["usage"] = self.usage.as_dict()
        if self.agent_identity is not None:
            payload["agent_identity"] = self.agent_identity.as_dict()
        return payload
