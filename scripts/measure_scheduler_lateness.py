"""Measure GitHub Actions schedule lateness without claiming scheduler origin.

The report compares the expected ``17 7 * * 2-6`` occurrence with the
workflow run's ``created_at`` and ``run_started_at`` timestamps.  It is a
read-only operational observation: GitHub metadata can show queueing and start
delay, but it cannot prove whether GitHub cron, Cloudflare, or a human caused a
workflow dispatch.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast

EXPECTED_CRON = "17 7 * * 2-6"
EXPECTED_REF = "main"
EXPECTED_WORKFLOW_PATH = ".github/workflows/forward-production.yml"
EXPECTED_WORKFLOW_ID = "forward-production.yml"
REPORT_SCHEMA = "scheduler-lateness.v1"
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_LIMIT = 30
DEFAULT_WARNING_SECONDS = 15 * 60
MAX_LOOKBACK_DAYS = 366
MAX_LIMIT = 100
_API_BASE = "https://api.github.com"
_MAX_API_RESPONSE_BYTES = 4 * 1024 * 1024
_RUN_ID = re.compile(r"[1-9][0-9]*")
_SHA = re.compile(r"[0-9a-f]{40}")
_SAFE_REASON = re.compile(r"^[a-z0-9_]+$")
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
_SCHEDULE_WEEKDAYS = frozenset({1, 2, 3, 4, 5})  # Tuesday through Saturday.


def expected_schedule_at(created_at: datetime) -> datetime:
    """Return the latest scheduled occurrence at or before ``created_at``."""

    current = _as_utc(created_at)
    candidate = datetime.combine(current.date(), time(7, 17), tzinfo=UTC)
    while candidate > current or candidate.weekday() not in _SCHEDULE_WEEKDAYS:
        candidate -= timedelta(days=1)
    return candidate


def fetch_scheduled_runs(
    *, repository: str, token: str, limit: int = DEFAULT_LIMIT
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    """Fetch bounded schedule-run metadata while returning value-free errors."""

    if not _valid_repository(repository):
        return [], ("invalid_repository",)
    if not token:
        return [], ("github_token_missing",)
    if not 1 <= limit <= MAX_LIMIT:
        return [], ("limit_invalid",)

    encoded_repository = urllib.parse.quote(repository, safe="/")
    query = urllib.parse.urlencode(
        {"event": "schedule", "branch": EXPECTED_REF, "per_page": str(limit)}
    )
    url = f"{_API_BASE}/repos/{encoded_repository}/actions/workflows/{EXPECTED_WORKFLOW_ID}/runs?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "edgar-moe-scheduler-lateness",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with _open_without_redirects(request) as response:
            if response.status != 200:
                return [], ("github_api_unexpected_status",)
            raw = response.read(_MAX_API_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError:
        return [], ("github_api_http_error",)
    except (urllib.error.URLError, TimeoutError, OSError):
        return [], ("github_api_unavailable",)

    if len(raw) > _MAX_API_RESPONSE_BYTES:
        return [], ("github_api_response_too_large",)
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], ("github_api_invalid_json",)
    if not isinstance(parsed, dict):
        return [], ("github_api_response_not_object",)
    runs = parsed.get("workflow_runs")
    if not isinstance(runs, list):
        return [], ("github_api_runs_not_list",)
    valid_runs = [cast(dict[str, Any], run) for run in runs if isinstance(run, dict)]
    errors = ("github_api_run_not_object",) if len(valid_runs) != len(runs) else ()
    return valid_runs, errors


def build_lateness_report(
    runs: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    warning_seconds: int = DEFAULT_WARNING_SECONDS,
    source_errors: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a redacted historical lateness report from run metadata."""

    checked = _as_utc(as_of)
    global_reasons = list(
        dict.fromkeys(
            reason if isinstance(reason, str) and _SAFE_REASON.fullmatch(reason) else "source_error"
            for reason in source_errors
        )
    )
    if not 1 <= lookback_days <= MAX_LOOKBACK_DAYS:
        global_reasons.append("lookback_days_invalid")
    if warning_seconds <= 0:
        global_reasons.append("warning_seconds_invalid")

    cutoff = checked - timedelta(days=max(lookback_days, 0))
    included: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    seen_run_ids: set[int] = set()
    out_of_window_count = 0

    for payload in runs:
        record, reasons = _redacted_observation(payload, checked=checked)
        created = _parse_timestamp(payload.get("created_at"))
        if not reasons and created is not None and created < cutoff:
            out_of_window_count += 1
            continue
        if not reasons and record["run_id"] in seen_run_ids:
            reasons = (*reasons, "duplicate_run_id")
        if reasons:
            invalid.append({**record, "reason_codes": list(reasons)})
        elif record["created_at"] is not None:
            included.append(record)
            seen_run_ids.add(cast(int, record["run_id"]))
        else:
            invalid.append({**record, "reason_codes": ["created_at_invalid"]})

    if invalid:
        global_reasons.append("invalid_run_metadata")
    if not included:
        global_reasons.append("no_in_scope_runs")

    included.sort(key=lambda item: (item["created_at"] or "", item["run_id"] or 0))
    schedule_delays = [int(item["schedule_delay_seconds"]) for item in included]
    queue_delays = [
        int(item["queue_delay_seconds"])
        for item in included
        if item["queue_delay_seconds"] is not None
    ]
    status = "observed" if included and not global_reasons else "blocked"
    return {
        "schema_version": REPORT_SCHEMA,
        "status": status,
        "workflow_path": EXPECTED_WORKFLOW_PATH,
        "ref": EXPECTED_REF,
        "cron_utc": EXPECTED_CRON,
        "checked_at": _format_timestamp(checked),
        "lookback_days": lookback_days,
        "warning_threshold_seconds": warning_seconds,
        "source": {
            "returned_run_count": len(runs),
            "included_run_count": len(included),
            "out_of_window_count": out_of_window_count,
            "invalid_run_count": len(invalid),
        },
        "aggregate": {
            "schedule_delay_seconds": _summarize(schedule_delays),
            "queue_delay_seconds": _summarize(queue_delays),
            "schedule_delay_warning_count": sum(
                delay > warning_seconds for delay in schedule_delays
            )
            if warning_seconds > 0
            else None,
            "runs_with_queue_delay": len(queue_delays),
        },
        "observations": included,
        "invalid_observations": invalid,
        "reason_codes": list(dict.fromkeys(global_reasons)),
        "scheduler_origin": "unproven_by_github_run_metadata",
        "note": (
            "GitHub run metadata measures observed schedule and queue delay only; it does not "
            "prove Cloudflare origin, a GitHub start-time SLO, or future punctuality."
        ),
    }


def _redacted_observation(
    payload: Mapping[str, Any], *, checked: datetime
) -> tuple[dict[str, Any], tuple[str, ...]]:
    reasons: list[str] = []
    run_id = _normalize_run_id(payload.get("id"))
    if run_id is None:
        reasons.append("run_id_invalid")
    if payload.get("event") != "schedule":
        reasons.append("event_not_schedule")
    if payload.get("path") != EXPECTED_WORKFLOW_PATH:
        reasons.append("workflow_path_mismatch")
    if payload.get("head_branch") != EXPECTED_REF:
        reasons.append("ref_mismatch")

    head_sha = _safe_sha(payload.get("head_sha"))
    if head_sha is None:
        reasons.append("head_sha_invalid")
    created = _parse_timestamp(payload.get("created_at"))
    if created is None:
        reasons.append("created_at_invalid")
    else:
        if created > checked + timedelta(minutes=5):
            reasons.append("created_at_in_future")
        scheduled = expected_schedule_at(created)
        schedule_delay = int((created - scheduled).total_seconds())
        if schedule_delay < 0:
            reasons.append("schedule_delay_negative")
    started = _parse_timestamp(payload.get("run_started_at"))
    if payload.get("run_started_at") is not None and started is None:
        reasons.append("run_started_at_invalid")
    if created is not None and started is not None and started < created:
        reasons.append("run_started_before_created")

    status = _safe_enum(payload.get("status"), _SAFE_STATUS)
    if status is None:
        reasons.append("status_invalid")
    conclusion = _safe_enum(payload.get("conclusion"), _SAFE_CONCLUSIONS)
    if payload.get("conclusion") is not None and conclusion is None:
        reasons.append("conclusion_invalid")

    scheduled_at = expected_schedule_at(created) if created is not None else None
    return (
        {
            "run_id": int(run_id) if run_id is not None else None,
            "head_sha": head_sha,
            "scheduled_at": _format_timestamp(scheduled_at),
            "created_at": _format_timestamp(created),
            "run_started_at": _format_timestamp(started),
            "schedule_delay_seconds": (
                int((created - scheduled_at).total_seconds())
                if created is not None and scheduled_at is not None
                else None
            ),
            "queue_delay_seconds": (
                int((started - created).total_seconds())
                if created is not None and started is not None and started >= created
                else None
            ),
            "run_status": status,
            "conclusion": conclusion,
        },
        tuple(dict.fromkeys(reasons)),
    )


def _summarize(values: Sequence[int]) -> dict[str, int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": ordered[(len(ordered) - 1) // 2],
        "p95": ordered[max(math.ceil(len(ordered) * 0.95) - 1, 0)],
        "max": ordered[-1],
    }


def _read_fixture(path: Path) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [], ("fixture_read_failed",)
    parsed_runs = parsed.get("workflow_runs") if isinstance(parsed, dict) else parsed
    if not isinstance(parsed_runs, list):
        return [], ("fixture_runs_not_list",)
    runs = [cast(dict[str, Any], run) for run in parsed_runs if isinstance(run, dict)]
    errors = ("fixture_run_not_object",) if len(runs) != len(parsed_runs) else ()
    return runs, errors


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


def _safe_sha(value: object) -> str | None:
    return value if isinstance(value, str) and _SHA.fullmatch(value) else None


def _safe_enum(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _parse_as_of(value: str | None) -> datetime | None:
    if value is None:
        return datetime.now(UTC)
    return _parse_timestamp(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="redacted JSON report path")
    parser.add_argument(
        "--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="lookback window"
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help=f"maximum runs to inspect (1-{MAX_LIMIT})"
    )
    parser.add_argument(
        "--warning-seconds",
        type=int,
        default=DEFAULT_WARNING_SECONDS,
        help="diagnostic schedule-delay warning threshold",
    )
    parser.add_argument("--as-of", help="optional UTC timestamp for reproducible offline reports")
    parser.add_argument("--run-json", type=Path, help="offline workflow-runs fixture")
    args = parser.parse_args()

    as_of = _parse_as_of(args.as_of)
    source_errors: list[str] = []
    if as_of is None:
        as_of = datetime.now(UTC)
        source_errors.append("as_of_invalid")
    if args.run_json is not None:
        runs, fixture_errors = _read_fixture(args.run_json)
        source_errors.extend(fixture_errors)
    else:
        runs, api_errors = fetch_scheduled_runs(
            repository=os.environ.get("GITHUB_REPOSITORY", ""),
            token=os.environ.get("GITHUB_TOKEN", ""),
            limit=args.limit,
        )
        source_errors.extend(api_errors)

    report = build_lateness_report(
        runs,
        as_of=as_of,
        lookback_days=args.lookback_days,
        warning_seconds=args.warning_seconds,
        source_errors=source_errors,
    )
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError:
        print("scheduler lateness report could not write its report", file=sys.stderr)
        return 2

    if report["status"] != "observed":
        print("scheduler lateness measurement blocked", file=sys.stderr)
        return 1
    print("scheduler lateness measurement observed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
