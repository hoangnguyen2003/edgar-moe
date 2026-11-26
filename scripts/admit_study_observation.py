"""Verify a signed study dispatch and reserve its slot in isolated control storage."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.observe_external_study_delivery import FORMAT

DOMAIN = "edgar-moe/private-study-observation/v1"
REPOSITORY = "hoangnguyen2003/edgar-moe"
CLAIM_BUCKET = "edgar-moe-study-observation-claims"
HOUR = timedelta(hours=1)


class AdmissionError(ValueError):
    pass


def observation_event(mode: str) -> str:
    if mode == "":
        return "schedule"
    if mode == "cloudflare":
        return "workflow_dispatch"
    raise AdmissionError("study_delivery_mode_refused")


def active_slot(start: datetime, end: datetime, stamp: str, now: datetime) -> datetime:
    """Check the exact nominal time and return the end of the nominated hour."""
    try:
        scheduled = datetime.strptime(stamp, FORMAT).replace(tzinfo=UTC)
    except ValueError:
        raise AdmissionError("study_observation_slot_refused") from None
    hour = scheduled.replace(minute=0)
    if (
        start.utcoffset() != timedelta(0)
        or end.utcoffset() != timedelta(0)
        or start != start.replace(minute=0, second=0, microsecond=0)
        or end - start != 168 * HOUR
        or scheduled.strftime(FORMAT) != stamp
        or scheduled.minute != 17
        or scheduled.second != 0
        or not start <= scheduled < end
        or not scheduled <= now < hour + HOUR
    ):
        raise AdmissionError("study_observation_slot_refused")
    return hour + HOUR


def admit(env: dict[str, str], start: datetime, end: datetime, now: datetime) -> str | None:
    """Return the admitted timestamp, or None when a previous run owns the slot."""
    if (
        env.get("GITHUB_REPOSITORY") != REPOSITORY
        or env.get("GITHUB_REF") != "refs/heads/main"
        or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or env.get("GITHUB_RUN_ATTEMPT") != "1"
    ):
        raise AdmissionError("study_dispatch_provenance_refused")

    policy = env["STUDY_POLICY_SHA256"]
    target = env["STUDY_DEPLOYMENT_HOST_SHA256"]
    secret = env["STUDY_OBSERVATION_SIGNING_KEY"]
    run_id = env["GITHUB_RUN_ID"]
    inputs = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())["inputs"]
    stamp, signature = inputs["scheduled_at"], inputs["signature"]
    if (
        not isinstance(stamp, str)
        or not isinstance(signature, str)
        or not all(
            re.fullmatch(r"[a-f0-9]{64}", field) for field in (policy, target, secret, signature)
        )
        or not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
    ):
        raise AdmissionError("study_dispatch_configuration_refused")
    active_slot(start, end, stamp, now)
    start_text, end_text = (bound.strftime(FORMAT) for bound in (start, end))
    message = "\n".join((DOMAIN, start_text, end_text, policy, target, stamp)).encode()
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise AdmissionError("study_dispatch_signature_refused")

    account = env["STUDY_CLAIM_R2_ACCOUNT_ID"]
    access_key = env["STUDY_CLAIM_R2_ACCESS_KEY_ID"]
    access_secret = env["STUDY_CLAIM_R2_SECRET_ACCESS_KEY"]
    if (
        not re.fullmatch(r"[a-f0-9]{32}", account)
        or not re.fullmatch(r"[A-Za-z0-9]{20,64}", access_key)
        or not re.fullmatch(r"[A-Za-z0-9]{32,128}", access_secret)
    ):
        raise AdmissionError("study_claim_configuration_refused")
    object_key = f"issue290/{policy}/{target}/{start_text}/{stamp}.json"
    with TemporaryDirectory() as directory:
        body = Path(directory) / "claim.json"
        body.write_text(json.dumps({"run_id": int(run_id), "scheduled_at": stamp}))
        outcome = subprocess.run(
            [
                "aws",
                "s3api",
                "put-object",
                "--bucket",
                CLAIM_BUCKET,
                "--key",
                object_key,
                "--body",
                str(body),
                "--content-type",
                "application/json",
                "--if-none-match",
                "*",
                "--endpoint-url",
                f"https://{account}.r2.cloudflarestorage.com",
                "--region",
                "auto",
            ],
            env={
                "PATH": env["PATH"],
                "AWS_ACCESS_KEY_ID": access_key,
                "AWS_SECRET_ACCESS_KEY": access_secret,
                "AWS_CONFIG_FILE": "/dev/null",
                "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
                "AWS_EC2_METADATA_DISABLED": "true",
                "AWS_MAX_ATTEMPTS": "1",
                "AWS_PAGER": "",
            },
            capture_output=True,
            timeout=15,
        )
    if outcome.returncode == 0:
        return stamp
    if b"(PreconditionFailed) when calling the PutObject operation" in outcome.stderr:
        return None
    raise AdmissionError("study_slot_claim_failed")


def main() -> int:
    env = dict(os.environ)
    try:
        start = datetime.fromisoformat(env["STUDY_STARTS_AT"])
        end = datetime.fromisoformat(env["STUDY_EXPIRES_AT"])
        stamp = admit(env, start, end, datetime.now(UTC))
    except AdmissionError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (KeyError, OSError, TypeError, ValueError, subprocess.TimeoutExpired):
        print("Study observation admission refused.", file=sys.stderr)
        return 1
    if stamp is not None:
        print(f"scheduled_at={stamp}")
        print(f"starts_at={start.strftime(FORMAT)}")
        print(f"expires_at={end.strftime(FORMAT)}")
        print(f"policy_sha256={env['STUDY_POLICY_SHA256']}")
        print(f"host_sha256={env['STUDY_DEPLOYMENT_HOST_SHA256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
