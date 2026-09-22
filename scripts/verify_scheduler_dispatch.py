"""Verify a GitHub workflow-dispatch run without claiming scheduler provenance.

The verifier intentionally records a small allow-list of GitHub run metadata. A
successful check proves that the selected run has the expected workflow, ref,
event, and recent timestamp. GitHub's run record does not prove whether
Cloudflare, GitHub's cron, or a human initiated the dispatch, so that origin is
always recorded as unproven.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

EXPECTED_REF = "main"
EXPECTED_WORKFLOW_PATH = ".github/workflows/forward-production.yml"
REPORT_SCHEMA = "scheduler-dispatch-observation.v1"
_API_BASE = "https://api.github.com"
_MAX_API_RESPONSE_BYTES = 1024 * 1024
_RUN_ID = re.compile(r"[1-9][0-9]*")
_SHA = re.compile(r"[0-9a-f]{40}")
_SAFE_STATUS = frozenset(
    {
        "completed",
        "queued",
        "in_progress",
        "requested",
        "waiting",
        "pending",
    }
)
_SAFE_CONCLUSIONS = frozenset(
    {
        "success",
        "failure",
        "neutral",
        "cancelled",
        "skipped",
        "timed_out",
        "action_required",
        "startup_failure",
        "stale",
    }
)


def validate_dispatch_run(
    payload: Mapping[str, Any],
    *,
    run_id: str,
    now: datetime,
    max_age_minutes: int = 30,
) -> tuple[str, ...]:
    """Return stable reason codes for a selected workflow run.

    Only immutable routing metadata and timestamps are checked here. The
    caller must keep the returned codes value-free so API errors and secrets
    cannot enter a retained evidence artifact.
    """

    errors: list[str] = []
    normalized_run_id = _normalize_run_id(run_id)
    if normalized_run_id is None:
        errors.append("invalid_run_id")

    payload_id = _normalize_run_id(payload.get("id"))
    if normalized_run_id is None or payload_id != normalized_run_id:
        errors.append("payload_id_mismatch")

    if payload.get("event") != "workflow_dispatch":
        errors.append("event_not_workflow_dispatch")
    if payload.get("path") != EXPECTED_WORKFLOW_PATH:
        errors.append("workflow_path_mismatch")
    if payload.get("head_branch") != EXPECTED_REF:
        errors.append("ref_mismatch")

    head_sha = payload.get("head_sha")
    if not isinstance(head_sha, str) or _SHA.fullmatch(head_sha) is None:
        errors.append("head_sha_invalid")

    if max_age_minutes <= 0:
        errors.append("max_age_invalid")
    created_at = _parse_timestamp(payload.get("created_at"))
    if created_at is None:
        errors.append("created_at_invalid")
    else:
        current = _as_utc(now)
        age_seconds = (current - created_at).total_seconds()
        if age_seconds < -300:
            errors.append("created_at_in_future")
        elif age_seconds > max_age_minutes * 60:
            errors.append("created_at_stale")

    run_status = payload.get("status")
    if not isinstance(run_status, str) or run_status not in _SAFE_STATUS:
        errors.append("status_invalid")

    run_started_at = payload.get("run_started_at")
    if run_started_at is not None and _parse_timestamp(run_started_at) is None:
        errors.append("run_started_at_invalid")

    conclusion = payload.get("conclusion")
    if conclusion is not None and (
        not isinstance(conclusion, str) or conclusion not in _SAFE_CONCLUSIONS
    ):
        errors.append("conclusion_invalid")

    return tuple(dict.fromkeys(errors))


def build_redacted_report(
    payload: Mapping[str, Any] | None,
    *,
    run_id: str,
    checked_at: datetime,
    reason_codes: tuple[str, ...],
) -> dict[str, object]:
    """Build a value-redacted report from an API response.

    Unknown API fields are deliberately ignored. The returned object contains
    no token, response body, URL, or arbitrary user-provided value.
    """

    payload = payload or {}
    normalized_run_id = _normalize_run_id(run_id)
    created_at = _parse_timestamp(payload.get("created_at"))
    checked = _as_utc(checked_at)
    age_seconds: int | None = None
    if created_at is not None:
        age_seconds = max(0, int((checked - created_at).total_seconds()))

    return {
        "schema_version": REPORT_SCHEMA,
        "status": "passed" if not reason_codes else "failed",
        "run_id": int(normalized_run_id) if normalized_run_id is not None else None,
        "event": _safe_text(payload.get("event")),
        "workflow_path": _safe_text(payload.get("path")),
        "workflow_name": _safe_text(payload.get("name")),
        "ref": _safe_text(payload.get("head_branch")),
        "head_sha": _safe_sha(payload.get("head_sha")),
        "created_at": _format_timestamp(created_at),
        "run_started_at": _format_timestamp(_parse_timestamp(payload.get("run_started_at"))),
        "run_status": _safe_enum(payload.get("status"), _SAFE_STATUS),
        "conclusion": _safe_enum(payload.get("conclusion"), _SAFE_CONCLUSIONS),
        "checked_at": _format_timestamp(checked),
        "age_seconds": age_seconds,
        "reason_codes": list(reason_codes),
        "scheduler_origin": "unproven_by_github_run_metadata",
    }


def fetch_github_run(
    *, repository: str, token: str, run_id: str
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Fetch one run while returning only value-free failure codes."""

    normalized_run_id = _normalize_run_id(run_id)
    if normalized_run_id is None:
        return None, ("invalid_run_id",)
    if not _valid_repository(repository):
        return None, ("invalid_repository",)
    if not token:
        return None, ("github_token_missing",)

    encoded_repository = urllib.parse.quote(repository, safe="/")
    url = f"{_API_BASE}/repos/{encoded_repository}/actions/runs/{normalized_run_id}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "edgar-moe-scheduler-observer",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with _open_without_redirects(request) as response:
            if response.status != 200:
                return None, ("github_api_unexpected_status",)
            raw = response.read(_MAX_API_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError:
        return None, ("github_api_http_error",)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, ("github_api_unavailable",)

    if len(raw) > _MAX_API_RESPONSE_BYTES:
        return None, ("github_api_response_too_large",)
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, ("github_api_invalid_json",)
    if not isinstance(parsed, dict):
        return None, ("github_api_response_not_object",)
    return cast(dict[str, Any], parsed), ()


def _open_without_redirects(request: urllib.request.Request) -> Any:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=10)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _read_fixture(path: Path) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    try:
        raw = path.read_bytes()
        parsed = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, ("fixture_read_failed",)
    if not isinstance(parsed, dict):
        return None, ("fixture_not_object",)
    return cast(dict[str, Any], parsed), ()


def _normalize_run_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        candidate = str(value)
    elif isinstance(value, str):
        candidate = value
    else:
        return None
    return candidate if _RUN_ID.fullmatch(candidate) else None


def _valid_repository(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value))


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_text(value: object) -> str | None:
    if isinstance(value, str) and value.isprintable() and len(value) <= 200:
        return value
    return None


def _safe_sha(value: object) -> str | None:
    return value if isinstance(value, str) and _SHA.fullmatch(value) else None


def _safe_enum(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="GitHub Actions run ID to observe")
    parser.add_argument("--output", type=Path, required=True, help="redacted JSON report path")
    parser.add_argument(
        "--max-age-minutes",
        type=int,
        default=30,
        help="maximum accepted age of the dispatch run (default: 30)",
    )
    parser.add_argument(
        "--run-json",
        type=Path,
        help="offline run JSON fixture; skips the GitHub API and token requirement",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    checked_at = datetime.now(UTC)
    payload: dict[str, Any] | None = None
    reason_codes: tuple[str, ...]

    if args.max_age_minutes <= 0:
        reason_codes = ("max_age_invalid",)
    elif args.run_json is not None:
        payload, reason_codes = _read_fixture(args.run_json)
    else:
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        token = os.environ.get("GITHUB_TOKEN", "")
        payload, reason_codes = fetch_github_run(
            repository=repository,
            token=token,
            run_id=args.run_id,
        )

    if payload is not None:
        reason_codes = validate_dispatch_run(
            payload,
            run_id=args.run_id,
            now=checked_at,
            max_age_minutes=args.max_age_minutes,
        )

    report = build_redacted_report(
        payload,
        run_id=args.run_id,
        checked_at=checked_at,
        reason_codes=reason_codes,
    )
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError:
        print("scheduler dispatch observation could not write its report", file=sys.stderr)
        return 2

    if reason_codes:
        print("scheduler dispatch observation failed", file=sys.stderr)
        return 1
    print("scheduler dispatch observation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
