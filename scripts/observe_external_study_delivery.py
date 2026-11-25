"""Verify a source-free signed dispatch and emit a body-free receipt."""

import hashlib
import hmac
import json
import os
import re
from datetime import UTC, datetime, timedelta

DOMAIN = "edgar-moe/study-external-delivery/v1"
FORMAT = "%Y-%m-%dT%H:%M:%S.000Z"


def receipt(env: dict[str, str], now: datetime) -> dict:
    if env.get("GITHUB_RUN_ATTEMPT") != "1" or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise ValueError("External delivery provenance refused")
    start_text = env["STUDY_DELIVERY_STARTS_AT"]
    stamp_text = env["STUDY_DELIVERY_SCHEDULED_AT"]
    start = datetime.strptime(start_text, FORMAT).replace(tzinfo=UTC)
    stamp = datetime.strptime(stamp_text, FORMAT).replace(tzinfo=UTC)
    key = env["STUDY_DELIVERY_SIGNING_KEY"]
    if (
        start.strftime(FORMAT) != start_text
        or stamp.strftime(FORMAT) != stamp_text
        or start.minute != 0
        or start.second != 0
        or stamp.minute != 17
        or stamp.second != 0
        or not start <= stamp < start + timedelta(days=1)
        or not stamp <= now < stamp.replace(minute=0) + timedelta(hours=1)
        or not re.fullmatch(r"[a-f0-9]{64}", key)
    ):
        raise ValueError("External delivery window refused")
    message = f"{DOMAIN}\n{start_text}\n{stamp_text}".encode()
    signature = hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, env["STUDY_DELIVERY_SIGNATURE"]):
        raise ValueError("External delivery signature refused")
    return {
        "scope": "issue290_external_scheduler_delivery",
        "run_id": int(env["GITHUB_RUN_ID"]),
        "starts_at": start_text,
        "scheduled_at": stamp_text,
        "observed_at": now.isoformat(timespec="seconds"),
        "hourly_slot": (stamp - start) // timedelta(hours=1),
        "application_requests": 0,
        "provider_requests": 0,
        "measurement_pairs": 0,
    }


def main() -> int:
    try:
        result = receipt(dict(os.environ), datetime.now(UTC))
    except (KeyError, ValueError, TypeError):
        print("External study delivery refused")
        return 1
    print("study290_external_delivery " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
