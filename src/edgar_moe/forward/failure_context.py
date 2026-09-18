"""Allowlisted failure metadata for the scheduled prospective runner."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime


def build_failure_context(
    environment: Mapping[str, str], *, observed_at: datetime | None = None
) -> dict[str, object]:
    """Build failure metadata without copying secrets or arbitrary environment values."""
    observed = observed_at or datetime.now(UTC)
    return {
        "schema_version": 1,
        "observed_at": observed.astimezone(UTC).isoformat(),
        "workflow": "forward-production",
        "run_id": environment.get("GITHUB_RUN_ID", ""),
        "run_attempt": environment.get("GITHUB_RUN_ATTEMPT", ""),
        "commit": environment.get("GITHUB_SHA", ""),
        "ref": environment.get("GITHUB_REF_NAME", ""),
        "cutoff": environment.get("CUTOFF", ""),
        "device": environment.get("DEVICE", ""),
        "cycle_outcome": environment.get("CYCLE_OUTCOME", ""),
    }
