from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from edgar_moe.forward.failure_context import (
    build_failure_context,
    redact_sensitive_text,
    safe_exception_message,
)
from edgar_moe.forward.registry import RegistryStateError


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


def test_external_driver_errors_are_reduced_to_type_only() -> None:
    error = psycopg.OperationalError(
        "connection failed for postgresql://reader:super-secret@example.test/registry"
    )

    message = safe_exception_message(error)

    assert message == "OperationalError"
    assert "super-secret" not in message


def test_redact_sensitive_text_is_safe_for_registry_callers() -> None:
    message = redact_sensitive_text(
        "provider rejected https://reader:super-secret@example.test/api "
        "token=abcdef123"
    )

    assert "example.test" not in message
    assert "super-secret" not in message
    assert "abcdef123" not in message
    assert "<redacted>" in message


def test_internal_error_details_redact_urls_and_secret_assignments() -> None:
    error = RegistryStateError(
        "provider rejected postgresql://reader:super-secret@example.test/registry "
        "api_key=abcdef123 password='another-secret'"
    )

    message = safe_exception_message(error)

    assert message.startswith("RegistryStateError: provider rejected")
    assert "super-secret" not in message
    assert "example.test" not in message
    assert "abcdef123" not in message
    assert "another-secret" not in message
    assert "<redacted>" in message
