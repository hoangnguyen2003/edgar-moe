"""Verify a signed study dispatch and reserve its slot in isolated control storage."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from scripts.observe_external_study_delivery import FORMAT

DOMAIN = "edgar-moe/private-study-observation/v1"
REPOSITORY = "hoangnguyen2003/edgar-moe"
CLAIM_BRANCH = "ops/study-observation-claims"
CLAIM_REF = f"refs/heads/{CLAIM_BRANCH}"
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
    claim = hashlib.sha256(message).hexdigest()
    return stamp if put_once(env, claim, {"run_id": run_id, "scheduled_at": stamp}) else None


def github_request(
    env: dict[str, str], method: str, path: str, payload: dict[str, str] | None = None
) -> tuple[int, bytes]:
    token = env["GH_TOKEN"]
    if not re.fullmatch(r"[!-~]{1,1024}", token):
        raise AdmissionError("study_claim_configuration_refused")
    with closing(http.client.HTTPSConnection("api.github.com", timeout=10)) as connection:
        connection.request(
            method,
            f"/repos/{REPOSITORY}/{path}",
            body=json.dumps(payload) if payload is not None else None,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "edgar-moe-study-claim",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        response = connection.getresponse()
        limit = 16 * 1024
        body = response.read(limit + 1)
        if len(body) > limit:
            raise AdmissionError("study_claim_response_too_large")
        return response.status, body


def claim_contents(claim: str) -> str:
    return f"contents/study-observation-claims/{claim}.json"


def read_claim(env: dict[str, str], claim: str) -> bytes:
    path = claim_contents(claim)
    status, body = github_request(env, "GET", f"{path}?{urlencode({'ref': CLAIM_BRANCH})}")
    if status != 200:
        raise AdmissionError("study_slot_claim_failed")
    record = json.loads(body)
    if (
        record["type"] != "file"
        or record["encoding"] != "base64"
        or record["path"] != path.removeprefix("contents/")
    ):
        raise AdmissionError("study_slot_claim_failed")
    return base64.b64decode(record["content"].replace("\n", ""), validate=True)


def put_once(env: dict[str, str], claim: str, contents: dict[str, str | int]) -> bool:
    # The Contents API requires a blob SHA to replace an existing file.
    status, _ = github_request(
        env,
        "PUT",
        claim_contents(claim),
        {
            "branch": CLAIM_BRANCH,
            "message": "Record hosted study claim",
            "content": base64.b64encode(json.dumps(contents).encode()).decode(),
        },
    )
    if status == 201:
        return True
    if status in (409, 422):
        read_claim(env, claim)
        return False
    raise AdmissionError("study_slot_claim_failed")


def check_store(env: dict[str, str]) -> None:
    run_id = dispatch_run_id(env)
    status, _ = github_request(env, "GET", f"git/ref/{CLAIM_REF.removeprefix('refs/')}")
    if status == 404:
        sha = env["GITHUB_SHA"]
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise AdmissionError("study_claim_configuration_refused")
        status, _ = github_request(env, "POST", "git/refs", {"ref": CLAIM_REF, "sha": sha})
        if status != 201:
            raise AdmissionError("study_claim_branch_setup_failed")
    elif status != 200:
        raise AdmissionError("study_claim_branch_setup_failed")
    key = hashlib.sha256(f"issue290/setup/{run_id}".encode()).hexdigest()
    body: dict[str, str | int] = {"scope": "study_claim_store_setup", "run_id": run_id}
    if not put_once(env, key, body):
        raise AdmissionError("study_claim_store_setup_key_exists")
    duplicate = body | {"scope": "duplicate_probe"}
    if put_once(env, key, duplicate):
        raise AdmissionError("study_claim_store_duplicate_accepted")
    if json.loads(read_claim(env, key)) != body:
        raise AdmissionError("study_claim_store_readback_mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-store", action="store_true")
    args = parser.parse_args()
    env = dict(os.environ)
    try:
        if args.check_store:
            check_store(env)
            print('{"status":"conditional_claim_verified","store":"github","measurement_pairs":0}')
            return 0
        start = datetime.fromisoformat(env["STUDY_STARTS_AT"])
        end = datetime.fromisoformat(env["STUDY_EXPIRES_AT"])
        stamp = admit(env, start, end, datetime.now(UTC))
    except AdmissionError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (KeyError, OSError, TypeError, ValueError, http.client.HTTPException):
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
