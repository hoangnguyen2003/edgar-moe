"""Allowlisted failure metadata for the scheduled prospective runner."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime

_URL = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^\s,;)}\]]+",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?P<name>\b(?:access[_-]?key(?:[_-]?id)?|api[_-]?key|passwd|password|"
    r"secret(?:[_-]?access[_-]?key)?|token)\b)\s*(?P<separator>[:=])\s*"
    r"(?P<quote>[\"']?)(?P<value>[^\"'&\s,;}]+)(?P=quote)",
    re.IGNORECASE,
)


def safe_exception_message(error: BaseException) -> str:
    """Return a provider-neutral error suitable for a public run record.

    Third-party driver exceptions are reduced to their type because their text
    commonly embeds connection URLs, request targets, or credential material.
    Errors raised by this package retain a compact, value-redacted detail so
    operators still get useful state-machine diagnostics.
    """
    error_type = type(error).__name__
    detail = " ".join(str(error).split())
    if not detail or not type(error).__module__.startswith("edgar_moe."):
        return error_type
    detail = _URL.sub(r"\g<scheme><redacted-url>", detail)
    detail = _SECRET_ASSIGNMENT.sub(r"\g<name>\g<separator><redacted>", detail)
    return f"{error_type}: {detail}"[:1_000]


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
