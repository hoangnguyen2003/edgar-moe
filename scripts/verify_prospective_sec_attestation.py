"""Observe a pre-cutoff GitHub hash commitment after verifying private SEC bytes.

Run with python -m scripts.verify_prospective_sec_attestation. Live verification
requires --live and makes one anonymous GET to the pinned GitHub API endpoint.
The report records current GitHub metadata; it is not a signed timestamp or
independent proof of SEC completeness, security membership, or source rights.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from scripts.capture_prospective_sec_index import (
    CohortCaptureError,
    load_policy,
    verify_capture,
)

_ARTIFACT_ROOT = Path(__file__).resolve().parents[1] / "data" / "artifacts"
_MAX_RESPONSE_BYTES = 128 * 1024
_MAX_BODY_BYTES = 8 * 1024


class AttestationError(RuntimeError):
    """A GitHub commitment observation failed its evidence contract."""


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AttestationError("JSON contains duplicate fields")
        result[key] = value
    return result


def _timestamp(value: object) -> datetime:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is None
    ):
        raise AttestationError("GitHub timestamp is invalid")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise AttestationError("GitHub timestamp is invalid") from error


def _positive_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise AttestationError("GitHub identifier is invalid")
    return value


def validate_comment(
    comment: dict[str, Any],
    commitment: dict[str, Any],
    policy: dict[str, Any],
    *,
    comment_id: int,
    observed_at: datetime,
) -> dict[str, Any]:
    """Validate a response from GitHub; callers must authenticate its transport."""
    comment_id = _positive_id(comment_id)
    target = policy["github_attestation"]
    issue_path = f"{target['repository']}/issues/{target['issue_number']}"
    expected_url = f"https://github.com/{issue_path}#issuecomment-{comment_id}"
    if (
        _positive_id(comment.get("id")) != comment_id
        or comment.get("issue_url") != f"https://api.github.com/repos/{issue_path}"
        or comment.get("html_url") != expected_url
    ):
        raise AttestationError("comment is not on the predeclared issue")
    author = comment.get("user")
    if not isinstance(author, dict) or _positive_id(author.get("id")) != target["author_user_id"]:
        raise AttestationError("comment author does not match the policy")
    created = _timestamp(comment.get("created_at"))
    updated = _timestamp(comment.get("updated_at"))
    if updated != created:
        raise AttestationError("GitHub reports that the comment was edited")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None or created > observed_at:
        raise AttestationError("comment observation time is invalid")
    if (
        not _timestamp(policy["capture_not_before"])
        <= created
        < _timestamp(policy["capture_before"])
        <= _timestamp(policy["research_cutoff"])
    ):
        raise AttestationError("commitment was not posted in the predeclared window")
    captured = datetime.fromisoformat(commitment["captured_at"])
    # GitHub reports seconds; a capture followed by a comment within that same
    # second must not fail just because the local capture retained microseconds.
    if captured.tzinfo is None or captured.replace(microsecond=0) > created:
        raise AttestationError("comment predates the declared capture")
    if (
        commitment["study_id"] != policy["study_id"]
        or commitment["policy_sha256"] != hashlib.sha256(_canonical(policy)).hexdigest()
    ):
        raise AttestationError("commitment is not bound to the supplied policy")
    body = comment.get("body")
    if not isinstance(body, str) or len(body.encode()) > _MAX_BODY_BYTES:
        raise AttestationError("comment body is missing or exceeds the byte bound")
    try:
        published = json.loads(body, object_pairs_hook=_unique_object)
    except ValueError as error:
        raise AttestationError("comment body must be the commitment JSON") from error
    if not isinstance(published, dict) or _canonical(published) != _canonical(commitment):
        raise AttestationError("published commitment does not match the private capture")
    return {
        "schema_version": 1,
        "status": "github_commitment_observed_before_cutoff",
        "study_id": policy["study_id"],
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "comment_url": expected_url,
        "comment_created_at": created.isoformat(),
        "author_user_id": target["author_user_id"],
        "commitment_sha256": hashlib.sha256(_canonical(commitment)).hexdigest(),
        "policy_sha256": commitment["policy_sha256"],
        "index_sha256": commitment["index_sha256"],
        "filer_roster_sha256": commitment["filer_roster_sha256"],
        "evidence_scope": "current_github_metadata_and_published_hashes",
    }


def fetch_comment(repository: str, comment_id: int) -> dict[str, Any]:
    """One fixed-origin request; never accept credentials or redirect targets."""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository) is None:
        raise AttestationError("GitHub repository is invalid")
    comment_id = _positive_id(comment_id)
    url = f"https://api.github.com/repos/{repository}/issues/comments/{comment_id}"
    with (
        httpx.Client(
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "edgar-moe-attestation",
            },
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        ) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > _MAX_RESPONSE_BYTES:
                raise AttestationError("GitHub response exceeds the byte bound")
    decoded = json.loads(data, object_pairs_hook=_unique_object)
    if not isinstance(decoded, dict):
        raise AttestationError("GitHub response is not a comment object")
    return decoded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="allow one anonymous GitHub GET")
    parser.add_argument("--capture-dir", required=True, type=Path)
    parser.add_argument("--comment-id", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if not args.live:
            raise AttestationError("GitHub verification requires --live")
        _positive_id(args.comment_id)
        destination = args.output.resolve()
        if not destination.is_relative_to(_ARTIFACT_ROOT.resolve()) or destination.exists():
            raise AttestationError("output must be a new file under ignored data/artifacts")
        policy = load_policy()
        commitment = verify_capture(args.capture_dir, policy)
        comment = fetch_comment(policy["github_attestation"]["repository"], args.comment_id)
        report = validate_comment(
            comment, commitment, policy, comment_id=args.comment_id, observed_at=datetime.now(UTC)
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(report, output, sort_keys=True, indent=2)
            output.write("\n")
    except (
        AttestationError,
        CohortCaptureError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        httpx.HTTPError,
    ) as error:
        print(f"SEC commitment attestation failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
