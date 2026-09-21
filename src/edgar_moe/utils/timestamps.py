"""Dependency-free ISO-8601 parsing shared by evidence validators.

Validators with stricter contracts keep their own rules on top of, or instead
of, this helper: operator evidence and platform readiness require a UTC offset,
and drift-history reports accept date-only as-of values.
"""

from __future__ import annotations

from datetime import UTC, datetime


class TimestampFormatError(ValueError):
    """The value is not an ISO-8601 timestamp string."""


class NaiveTimestampError(ValueError):
    """The timestamp has no timezone offset, so its instant is ambiguous."""


def parse_aware_timestamp(value: object) -> datetime:
    """Parse an ISO-8601 string with an explicit offset and return it in UTC.

    ``Z`` is accepted as UTC. Error messages are phrased to follow a field label,
    for example ``f"{label} {error}"``.
    """
    if not isinstance(value, str) or not value.strip():
        raise TimestampFormatError("must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TimestampFormatError("must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NaiveTimestampError("must include a timezone")
    return parsed.astimezone(UTC)
