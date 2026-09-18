from __future__ import annotations

from datetime import UTC, datetime

from edgar_moe.forward.failure_context import build_failure_context


def test_failure_context_is_allowlisted_and_redacted() -> None:
    observed_at = datetime(2026, 9, 19, 1, 2, 3, tzinfo=UTC)
    context = build_failure_context(
        {
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_SHA": "deadbeef",
            "GITHUB_REF_NAME": "main",
            "CUTOFF": "2026-09-18",
            "DEVICE": "cpu",
            "CYCLE_OUTCOME": "failure",
            "EDGAR_MOE_REGISTRY_DATABASE_URL": "postgresql://user:secret@example.test/db",
        },
        observed_at=observed_at,
    )

    assert context == {
        "schema_version": 1,
        "observed_at": "2026-09-19T01:02:03+00:00",
        "workflow": "forward-production",
        "run_id": "123",
        "run_attempt": "2",
        "commit": "deadbeef",
        "ref": "main",
        "cutoff": "2026-09-18",
        "device": "cpu",
        "cycle_outcome": "failure",
    }
