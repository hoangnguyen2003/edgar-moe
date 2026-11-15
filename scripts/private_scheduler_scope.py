"""Admission for the finite private #156 observation, before any service access."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCOPE_SHA256 = "43ee2f95bb9b4feb68e311957a4e70b2d0e9bb8c630317903066a64eac56024e"
SCOPE_ID = "issue156-20261114"
SCOPE_PATH = "ops/issue156/private-scheduler-scope-20261114.json"
WORKFLOW = "forward-private-scheduler.yml"
CRON = "17 7 * * TUE-SAT"
DATES = ("2026-11-18", "2026-11-19", "2026-11-20")
PROBE_DATE = "2026-11-17"


def message(scheduled_at: str, mode: str = "forecast") -> bytes:
    return f"issue156-private-v2\n{SCOPE_ID}\n{mode}\n{scheduled_at}\n{CRON}\nmain\ncpu\n{WORKFLOW}".encode()


def admit_activation(now: datetime) -> None:
    deadline = datetime.fromisoformat(PROBE_DATE + "T07:17:00+00:00") - timedelta(hours=1)
    if now.utcoffset() is None or now >= deadline:
        raise ValueError("Fresh activation must precede the delivery slot by at least one hour")


def admit(
    inputs: dict[str, Any],
    *,
    key: str,
    now: datetime,
    event: str,
    ref: str,
    allow_delivery_probe: bool = False,
) -> datetime:
    if (
        event != "workflow_dispatch"
        or ref != "refs/heads/main"
        or re.fullmatch(r"[a-f0-9]{64}", key) is None
    ):
        raise ValueError("Private scheduler context refused")
    mode = inputs.get("mode")
    if not isinstance(mode, str):
        raise ValueError("Private scheduler mode refused")
    dates = (
        DATES
        if mode == "forecast"
        else (PROBE_DATE,)
        if mode == "delivery_probe" and allow_delivery_probe
        else ()
    )
    stamp = inputs.get("scheduled_at", "")
    if not isinstance(stamp, str) or stamp not in {f"{day}T07:17:00.000Z" for day in dates}:
        raise ValueError("Outside the reviewed nominal schedule slots for this mode")
    if inputs.get("device") != "cpu":
        raise ValueError("Only CPU inference is in scope")
    signature = inputs.get("signature", "")
    expected = hmac.new(key.encode(), message(stamp, mode), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError("Scheduler origin signature refused")
    scheduled = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if now.tzinfo is None or not timedelta(0) <= now - scheduled <= timedelta(minutes=90):
        raise ValueError("Future or stale scheduler dispatch refused")
    return scheduled.astimezone(UTC)


def read_scope(root: Path) -> dict[str, Any]:
    path = root / SCOPE_PATH
    if hashlib.sha256(path.read_bytes()).hexdigest() != SCOPE_SHA256:
        raise ValueError("Reviewed private scheduler scope changed")
    record: dict[str, Any] = json.loads(path.read_text())
    if (
        record["scheduled_dates_utc"] != list(DATES)
        or record["cron"] != CRON
        or record["delivery_probe_date_utc"] != PROBE_DATE
    ):
        raise ValueError("Private schedule bounds changed")
    for relative, expected in record["bound_input_sha256"].items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Bound private runtime input changed: {relative}")
    return record
