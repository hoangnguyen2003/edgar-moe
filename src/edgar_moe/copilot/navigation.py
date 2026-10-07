"""Bounded, snapshot-only explanations; separate from the frozen v1 benchmark arm.

These templates display evidence, not model-generated reasoning. Keyword routing
is deliberately limited and does not establish general question-answering utility.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime

from .contracts import MAX_QUESTION_BYTES, Citation, CopilotAnswer, ToolTrace, content_hash
from .tools import ReadOnlyToolset
from .verification import verify_copilot_answer_report

NAVIGATOR_VERSION = "deterministic-evidence-explanations-v1"
_MAX_ROWS = 12
_MAX_TEXT = 600
_ROUTES = (
    (
        r"\b(summary|metrics?|costs?|scenarios?|portfolio|predictive|signal|tradable|locked|performance)\b",
        "get_study_summary",
    ),
    (r"\b(target|split|methodology|limitations?|horizon|signal|tradable)\b", "get_methodology"),
    (
        r"\b(experiments?|baselines?|model[- ]selection|selected|selection|outperform)\b",
        "get_experiment_results",
    ),
    (r"\b(identity|hash|snapshot|locked|rights?|licens\w*)\b", "get_frozen_identity"),
    (
        r"\b(governance|controls?|enforced|pending|rights?|licens\w*|forward|backups?)\b",
        "get_governance_status",
    ),
)
_ORDER = re.compile(
    r"\b(buy|sell|purchase)\b|\bshort\s+(shares?|stocks?|positions?)\b|"
    r"\b(place|execute|submit|send|recommend|trade)\b.*"
    r"\b(trades?|orders?|shares?|stocks?|positions?|allocation)\b"
)
_LIVE = re.compile(r"\b(live|latest|current|today|now|real[- ]time)\b")
_QUOTE = re.compile(r"\b(quotes?|prices?)\b")


class DeterministicEvidenceNavigator:
    """At most five zero-argument snapshot projections with fixed explanations."""

    def __init__(self, toolset: ReadOnlyToolset) -> None:
        if any(
            value is not None
            for value in (
                toolset.registry,
                toolset.diagnostic_path,
                toolset.diagnostic_history_path,
            )
        ):
            raise ValueError("deterministic explanations accept snapshot-only tools")
        self.toolset = toolset

    def ask(self, question: str) -> CopilotAnswer:
        question = question.strip()
        if not question or len(question.encode("utf-8")) > MAX_QUESTION_BYTES:
            raise ValueError("question must be non-empty and within the copilot byte limit")
        lowered = " ".join(question.lower().split())
        if _ORDER.search(lowered):
            return self._answer(
                question,
                "I cannot place or execute orders, recommend a position, or provide trading "
                "instructions. This navigator only explains local research evidence.",
            )
        if _LIVE.search(lowered) and _QUOTE.search(lowered):
            return self._answer(
                question,
                "Live prices and current quotes are unavailable: this navigator has no market "
                "feed and makes no network requests. The local synthetic snapshot is not a quote.",
            )
        names = {name for pattern, name in _ROUTES if re.search(pattern, lowered)}
        if "get_experiment_results" in names:
            names.add("get_study_summary")
        if not names:
            return self._answer(
                question,
                "No fixed evidence route matches this question. Supported topics: study metrics "
                "and costs, target and split, experiments, snapshot identity, and governance.",
            )
        sections = [
            "Synthetic demonstration only; these values test the software path, not real-world "
            "alpha, tradability, or investment performance. Fixed topic templates follow; they "
            "may not answer every part of your question."
        ]
        citations: list[Citation] = []
        trace: list[ToolTrace] = []
        for _, name in _ROUTES:
            if name not in names:
                continue
            result = self.toolset.execute(name, {})
            digest = content_hash(result.payload)
            if (
                result.name != name
                or len(result.citations) != 1
                or any(citation.evidence_sha256 != digest for citation in result.citations)
            ):
                raise ValueError("snapshot explanation requires exact, cited tool evidence")
            index = len(trace) + 1
            sections.append(f"[{index}] {name}\n" + _RENDERERS[name](_object(result.payload)))
            citations.extend(result.citations)
            trace.append(ToolTrace(index, name, content_hash({}), digest, 1))
        return self._answer(question, "\n\n".join(sections), tuple(citations), tuple(trace))

    def _answer(
        self,
        question: str,
        text: str,
        citations: tuple[Citation, ...] = (),
        trace: tuple[ToolTrace, ...] = (),
    ) -> CopilotAnswer:
        if len(text.encode("utf-8")) > 64_000:
            raise ValueError("snapshot explanation exceeds its answer budget")
        result = CopilotAnswer(
            question=question,
            answer=text,
            model=NAVIGATOR_VERSION,
            provider="none",
            created_at=datetime.now(UTC).isoformat(),
            frozen_identity=self.toolset.repository.frozen_identity(),
            citations=citations,
            trace=trace,
            evidence_status="grounded" if citations else "uncited",
        )
        verify_copilot_answer_report(result.as_dict())
        return result


def _object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return "not recorded"
    # Quoted, bounded source values are data, never instructions to follow.
    value = " ".join(value.split())
    if len(value) > _MAX_TEXT:
        value = value[:_MAX_TEXT] + "… [truncated]"
    return json.dumps(value, ensure_ascii=True)


def _number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "not recorded"
    try:
        if not math.isfinite(value):
            return "not recorded"
    except OverflowError:
        return "not recorded"
    return f"{value:.6g}"


def _boolean(value: object) -> str:
    return "true" if value is True else "false" if value is False else "not recorded"


def _rows(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _overflow(rows: list[object]) -> list[str]:
    return (
        [f"Only the first {_MAX_ROWS} source rows are displayed."] if len(rows) > _MAX_ROWS else []
    )


def _summary(payload: dict[str, object]) -> str:
    lines = ["Predictive metrics (return units; rank IC is unitless):"]
    metrics = _object(payload.get("predictive_metrics"))
    for split in ("validation", "locked_test"):
        row = _object(metrics.get(split))
        lines.append(
            f"{split}: RMSE={_number(row.get('rmse'))}; MAE={_number(row.get('mae'))}; "
            f"rank IC={_number(row.get('rank_ic'))}."
        )
    rows = _rows(payload.get("portfolio_scenarios"))
    lines.append("Cost scenarios (returns and hit rates are fractions, not percentages):")
    for item in rows[:_MAX_ROWS]:
        row = _object(item)
        lines.append(
            f"Cost {_number(row.get('cost_bps'))} bps: annualized return="
            f"{_number(row.get('annualized_return'))}; Sharpe={_number(row.get('sharpe'))}; "
            f"hit rate={_number(row.get('hit_rate'))}; Sharpe interval="
            f"[{_number(row.get('sharpe_ci_low'))}, {_number(row.get('sharpe_ci_high'))}]."
        )
    if not rows:
        lines.append("No cost-scenario rows recorded.")
    lines.extend(_overflow(rows))
    lines.append(
        "Prediction metrics alone do not establish a profitable strategy. A synthetic locked-test "
        "label is not access to the withheld historical v1 result."
    )
    return "\n".join(lines)


def _methodology(payload: dict[str, object]) -> str:
    lines = [
        f"Source {key}: {_text(payload.get(key))}."
        for key in ("target", "split", "model", "portfolio", "costs")
    ]
    rows = _rows(payload.get("limitations"))
    lines.append("Source limitations:")
    lines.extend(_text(row) for row in rows[:_MAX_ROWS])
    if not rows:
        lines.append("No limitation entries recorded; this is not proof of absence of limitations.")
    lines.extend(_overflow(rows))
    return "\n".join(lines)


def _experiments(payload: dict[str, object]) -> str:
    rows = _rows(payload.get("experiments"))
    lines = ["Recorded experiment rows:"]
    for item in rows[:_MAX_ROWS]:
        row = _object(item)
        lines.append(
            f"{_text(row.get('name'))}: selected={_boolean(row.get('selected'))}; "
            f"validation RMSE={_number(row.get('validation_rmse'))}; "
            f"validation rank IC={_number(row.get('validation_rank_ic'))}; "
            f"locked-test RMSE={_number(row.get('locked_test_rmse'))}."
        )
    if not rows:
        lines.append("No experiment rows recorded.")
    lines.extend(_overflow(rows))
    lines.append(
        "A selected flag records a choice, not why it was made or proof of superiority. Missing "
        "model metrics cannot support rankings; no selection rationale is inferred here."
    )
    return "\n".join(lines)


def _identity(payload: dict[str, object]) -> str:
    return (
        f"Published snapshot SHA-256: {_text(payload.get('sha256'))}.\n"
        f"As of: {_text(payload.get('as_of'))}; data mode: {_text(payload.get('data_mode'))}.\n"
        f"Historical selection hash: {_text(payload.get('selection_hash'))}; "
        f"historical locked-test hash: {_text(payload.get('locked_test_hash'))}.\n"
        "The snapshot digest identifies bytes; citation digests identify individual tool payloads. "
        "Neither proves licensing, external backup coverage, predictive quality, or tradability. "
        "Historical v1 results are not supplied by this synthetic snapshot."
    )


def _governance(payload: dict[str, object]) -> str:
    rows = _rows(payload.get("controls"))
    lines = ["Recorded control statuses (not a live provider-console audit):"]
    for item in rows[:_MAX_ROWS]:
        row = _object(item)
        lines.append(
            f"{_text(row.get('key'))}: status={_text(row.get('status'))}; "
            f"owner={_text(row.get('owner'))}; source summary={_text(row.get('summary'))}."
        )
    if not rows:
        lines.append("No control rows recorded.")
    lines.extend(_overflow(rows))
    public = _object(payload.get("public_data"))
    lines.append(
        f"Historical v1 served by application="
        f"{_boolean(public.get('historical_v1_served_by_application'))}; "
        f"redistribution status={_text(public.get('redistribution_status'))}."
    )
    forward = _object(payload.get("forward_status"))
    lines.append(
        f"Forward registry attached to this navigator={_boolean(forward.get('configured'))}; "
        f"available={_boolean(forward.get('available'))}. No database was queried; this does not "
        "establish the actual production registry state."
    )
    lines.append("Pending evidence is not an approval or a verified provider control.")
    return "\n".join(lines)


_RENDERERS = {
    "get_study_summary": _summary,
    "get_methodology": _methodology,
    "get_experiment_results": _experiments,
    "get_frozen_identity": _identity,
    "get_governance_status": _governance,
}
