"""Admission for the finite private #156 observation, before any service access."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCOPE_SHA256 = "0e5d81c5e005be948fa8deb71f1466248ab4620de4b43529b3ab3a609d83ad95"
SCOPE_ID = "issue156-20261106"
WORKFLOW = "forward-private-scheduler.yml"
CRON = "17 7 * * TUE-SAT"
DATES = ("2026-11-07", "2026-11-10", "2026-11-11")


def message(scheduled_at: str) -> bytes:
    return f"issue156-private-v1\n{scheduled_at}\n{CRON}\nmain\ncpu\n{WORKFLOW}".encode()


def admit(inputs: dict[str, Any], *, key: str, now: datetime, event: str, ref: str) -> datetime:
    if event != "workflow_dispatch" or ref != "refs/heads/main" or len(key) != 64:
        raise ValueError("Private scheduler context refused")
    stamp = inputs.get("scheduled_at", "")
    if not isinstance(stamp, str) or stamp not in {f"{day}T07:17:00.000Z" for day in DATES}:
        raise ValueError("Outside the three reviewed nominal schedule slots")
    if inputs.get("device") != "cpu":
        raise ValueError("Only CPU inference is in scope")
    signature = inputs.get("signature", "")
    expected = hmac.new(key.encode(), message(stamp), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError("Scheduler origin signature refused")
    scheduled = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if now.tzinfo is None or not timedelta(0) <= now - scheduled <= timedelta(minutes=90):
        raise ValueError("Future or stale scheduler dispatch refused")
    return scheduled.astimezone(UTC)


def read_scope(root: Path) -> dict[str, Any]:
    path = root / "ops/issue156/private-scheduler-scope-20261106.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != SCOPE_SHA256:
        raise ValueError("Reviewed private scheduler scope changed")
    record: dict[str, Any] = json.loads(path.read_text())
    if record["scheduled_dates_utc"] != list(DATES) or record["cron"] != CRON:
        raise ValueError("Private schedule bounds changed")
    for relative, expected in record["bound_input_sha256"].items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Bound private runtime input changed: {relative}")
    return record
