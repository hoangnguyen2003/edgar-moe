"""Fail-closed source-use review gate for provider-backed research operations.

This is an operational hold, not a legal determination. Clearance must be
recorded in a reviewed change; there is deliberately no runtime environment
variable or CLI option that can bypass it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SOURCE_USE_REVIEW_ISSUE = 280

# Keep this decision explicit and code-reviewed. A future clearance must cite
# its source-bound evidence in a separate reviewed PR before changing the state.
SOURCE_USE_REVIEW_STATUS: Literal["unresolved", "cleared"] = "unresolved"
SOURCE_USE_REVIEW_EVIDENCE: str | None = None

SOURCE_USE_HELD_COMMANDS = frozenset(
    {
        "ingest-assets",
        "build-universe",
        "record-universe-capture",
        "refresh-data",
        "screen-universe",
        "build-dataset",
        "research-drift",
        "research-drift-history",
        "research-drift-readiness",
        "run-study",
        "walk-forward-study",
        "v2-pretest-review",
        "open-frozen-test",
        "forward-forecast",
        "forward-settle",
    }
)


class SourceUseReviewRequired(RuntimeError):
    """Raised when a provider-backed operation is held pending review."""


@dataclass(frozen=True)
class SourceUseReviewDecision:
    status: Literal["unresolved", "cleared"]
    evidence: str | None


SOURCE_USE_DECISION = SourceUseReviewDecision(
    status=SOURCE_USE_REVIEW_STATUS,
    evidence=SOURCE_USE_REVIEW_EVIDENCE,
)


def require_source_use_clearance(operation: str) -> None:
    """Stop held operations unless a reviewed, evidence-linked clearance exists."""

    decision = SOURCE_USE_DECISION
    if decision.status == "cleared" and decision.evidence:
        return
    raise SourceUseReviewRequired(
        f"{operation} is held pending source-use review in issue "
        f"#{SOURCE_USE_REVIEW_ISSUE}. There is no runtime bypass; resume only in a "
        "reviewed change citing source-bound written clearance or an approved replacement. "
        "The deterministic synthetic demo remains available."
    )
