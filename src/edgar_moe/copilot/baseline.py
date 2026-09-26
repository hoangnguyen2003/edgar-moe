"""Credential-free, read-only evidence-navigation baseline for copilot evaluation.

This baseline routes by a fixed keyword policy. It does not synthesize research
claims, contact a model provider, or change the frozen study. Its deliberately
plain structured answers make incremental LLM usefulness a testable question.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import orjson

from .contracts import MAX_QUESTION_BYTES, CopilotAnswer, ToolTrace, content_hash
from .tools import ReadOnlyToolset
from .verification import verify_copilot_answer_report

_TRADE_ACTION = re.compile(r"\b(place|execute|submit|buy|sell)\b")
_TRADE_OBJECT = re.compile(r"\b(trade|order|shares?|ticker|stocks?|positions?)\b")
_ROUTES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(governance|controls?|enforced|pending)\b"), "get_governance_status"),
    (re.compile(r"\b(target|split|methodology|limitations?)\b"), "get_methodology"),
    (re.compile(r"\b(experiments?|baselines?|model[- ]selection)\b"), "get_experiment_results"),
    (re.compile(r"\b(identity|hash|snapshot)\b"), "get_frozen_identity"),
    (
        re.compile(r"\b(summary|metrics?|costs?|scenarios?|portfolio|predictive)\b"),
        "get_study_summary",
    ),
)
_MAX_ANSWER_BYTES = 256_000


class DeterministicEvidenceBaseline:
    """A fixed, abstaining comparison arm over the same read-only tool surface."""

    def __init__(self, toolset: ReadOnlyToolset) -> None:
        self.toolset = toolset

    def ask(self, question: str) -> CopilotAnswer:
        normalized = question.strip()
        if not normalized or len(normalized.encode("utf-8")) > MAX_QUESTION_BYTES:
            raise ValueError("question must be non-empty and within the copilot byte limit")
        lowered = normalized.lower()
        if _TRADE_ACTION.search(lowered) and _TRADE_OBJECT.search(lowered):
            return self._uncited(normalized, "I cannot place trades or give an order instruction.")
        for pattern, tool_name in _ROUTES:
            if pattern.search(lowered):
                result = self.toolset.execute(tool_name, {})
                answer = (
                    f"Direct read-only evidence from {tool_name}; review the cited fields:\n"
                    + orjson.dumps(result.payload, option=orjson.OPT_SORT_KEYS).decode("utf-8")
                )
                if len(answer.encode("utf-8")) > _MAX_ANSWER_BYTES:
                    raise ValueError("routed evidence exceeds the baseline answer limit")
                envelope = CopilotAnswer(
                    question=normalized,
                    answer=answer,
                    model="deterministic-evidence-navigation-v1",
                    provider="none",
                    created_at=datetime.now(UTC).isoformat(),
                    frozen_identity=self.toolset.repository.frozen_identity(),
                    citations=result.citations,
                    trace=(
                        ToolTrace(
                            call_index=1,
                            name=tool_name,
                            arguments_sha256=content_hash({}),
                            result_sha256=content_hash(result.payload),
                            citation_count=len(result.citations),
                        ),
                    ),
                    evidence_status="grounded",
                )
                verify_copilot_answer_report(envelope.as_dict())
                return envelope
        return self._uncited(normalized, "No fixed evidence route matches this question.")

    def _uncited(self, question: str, answer: str) -> CopilotAnswer:
        envelope = CopilotAnswer(
            question=question,
            answer=answer,
            model="deterministic-evidence-navigation-v1",
            provider="none",
            created_at=datetime.now(UTC).isoformat(),
            frozen_identity=self.toolset.repository.frozen_identity(),
            citations=(),
            trace=(),
            evidence_status="uncited",
        )
        verify_copilot_answer_report(envelope.as_dict())
        return envelope
