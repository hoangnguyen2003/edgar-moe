"""Verify a signed study dispatch and reserve its slot in isolated control storage."""

from __future__ import annotations

import argparse
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


def dispatch_run_id(env: dict[str, str]) -> int:
    if (
        env.get("GITHUB_REPOSITORY") != REPOSITORY
        or env.get("GITHUB_REF") != "refs/heads/main"
        or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or env.get("GITHUB_RUN_ATTEMPT") != "1"
    ):
        raise AdmissionError("study_dispatch_provenance_refused")
    run_id = env.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", run_id):
        raise AdmissionError("study_dispatch_provenance_refused")
    return int(run_id)


def admit(env: dict[str, str], start: datetime, end: datetime, now: datetime) -> str | None:
    """Return the admitted timestamp, or None when a previous run owns the slot."""
    run_id = dispatch_run_id(env)
    policy = env["STUDY_POLICY_SHA256"]
    target = env["STUDY_DEPLOYMENT_HOST_SHA256"]
    secret = env["STUDY_OBSERVATION_SIGNING_KEY"]
    inputs = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())["inputs"]
    stamp, signature = inputs["scheduled_at"], inputs["signature"]
    if (
        not isinstance(stamp, str)
        or not isinstance(signature, str)
        or not all(
            re.fullmatch(r"[a-f0-9]{64}", field) for field in (policy, target, secret, signature)
        )
    ):
        raise AdmissionError("study_dispatch_configuration_refused")
    active_slot(start, end, stamp, now)
    start_text, end_text = (bound.strftime(FORMAT) for bound in (start, end))
    message = "\n".join((DOMAIN, start_text, end_text, policy, target, stamp)).encode()
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise AdmissionError("study_dispatch_signature_refused")
    object_key = f"issue290/{policy}/{target}/{start_text}/{stamp}.json"
    return stamp if put_once(env, object_key, {"run_id": run_id, "scheduled_at": stamp}) else None


def put_once(env: dict[str, str], object_key: str, contents: dict[str, str | int]) -> bool:
    account = env["STUDY_CLAIM_R2_ACCOUNT_ID"]
    access_key = env["STUDY_CLAIM_R2_ACCESS_KEY_ID"]
    access_secret = env["STUDY_CLAIM_R2_SECRET_ACCESS_KEY"]
    if (
        not re.fullmatch(r"[a-f0-9]{32}", account)
        or not re.fullmatch(r"[A-Za-z0-9]{20,64}", access_key)
        or not re.fullmatch(r"[A-Za-z0-9]{32,128}", access_secret)
    ):
        raise AdmissionError("study_claim_configuration_refused")
    with TemporaryDirectory() as directory:
        body = Path(directory) / "claim.json"
        body.write_text(json.dumps(contents))
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
        return True
    if b"(PreconditionFailed) when calling the PutObject operation" in outcome.stderr:
        return False
    raise AdmissionError("study_slot_claim_failed")


def check_store(env: dict[str, str]) -> None:
    run_id = dispatch_run_id(env)
    key = f"issue290/setup/{run_id}.json"
    body: dict[str, str | int] = {"scope": "study_claim_store_setup", "run_id": run_id}
    if not put_once(env, key, body):
        raise AdmissionError("study_claim_store_setup_key_exists")
    if put_once(env, key, body):
        raise AdmissionError("study_claim_store_duplicate_accepted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-store", action="store_true")
    args = parser.parse_args()
    env = dict(os.environ)
    try:
        if args.check_store:
            check_store(env)
            print(
                '{"status":"conditional_claim_verified","storage_requests":2,"measurement_pairs":0}'
            )
            return 0
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
