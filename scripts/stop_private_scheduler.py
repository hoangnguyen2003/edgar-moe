"""Dispatch one fixed STOP and verify its exact run; queued is never stopped.

Uses only Actions metadata and the caller's existing Actions credential. The
management workflow alone holds Cloudflare credentials. No forecast is invoked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from private_scheduler_scope import DATES, SCOPE_ID, read_scope

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "hoangnguyen2003/edgar-moe"
WORKFLOW = "deploy-private-scheduler.yml"
PREFIX = f"repos/{REPOSITORY}/actions"


class Refusal(ValueError):
    pass


def api(path: str, body: dict[str, Any] | None = None) -> Any:
    command = ["gh", "api", "--hostname", "github.com", "-H", "X-GitHub-Api-Version: 2026-03-10"]
    if body is not None:
        command += ["--method", "POST", "--input", "-"]
    command.append(path)
    try:
        run = subprocess.run(
            command,
            input=json.dumps(body).encode() if body is not None else None,
            capture_output=True,
            timeout=15,
        )
        if run.returncode or len(run.stdout) > 65536:
            raise Refusal("actions_request_refused")
        return json.loads(run.stdout)
    except Refusal:
        raise
    except Exception:
        raise Refusal("actions_request_refused") from None


def stop_scope(
    *,
    now: datetime,
    context: dict[str, str],
    request: Callable[..., Any] = api,
    pause: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope_id": SCOPE_ID,
        "status": "refused",
        "stop_stage": "STOP",
        "stop_verified": False,
        "dispatch_requests": 0,
        "metadata_requests": 0,
        "forecast_requests": 0,
        "cloudflare_credentials_in_coordinator": False,
        "issue_closed": False,
    }
    try:
        read_scope(ROOT)
        if (
            context.get("GITHUB_REPOSITORY") != REPOSITORY
            or context.get("GITHUB_REF") != "refs/heads/main"
            or now.utcoffset() is None
            or now < datetime.fromisoformat(DATES[-1] + "T07:17:00+00:00")
        ):
            raise Refusal("cleanup_context_or_date_refused")
        report["dispatch_requests"] = 1
        receipt = request(
            f"{PREFIX}/workflows/{WORKFLOW}/dispatches",
            {"ref": "main", "inputs": {"stage": "STOP"}},
        )
        identifier = receipt.get("workflow_run_id")
        if (
            type(identifier) is not int
            or identifier <= 0
            or receipt.get("run_url") != f"https://api.github.com/{PREFIX}/runs/{identifier}"
            or receipt.get("html_url")
            != f"https://github.com/{REPOSITORY}/actions/runs/{identifier}"
        ):
            raise Refusal("stop_dispatch_receipt_refused")
        report["stop_run_id"] = identifier
        deadline = clock() + 180
        for _ in range(36):
            if clock() >= deadline:
                break
            report["metadata_requests"] += 1
            run = request(f"{PREFIX}/runs/{identifier}")
            if (
                type(run.get("id")) is not int
                or run.get("id") != identifier
                or run.get("head_branch") != "main"
                or run.get("event") != "workflow_dispatch"
                or run.get("path") != ".github/workflows/" + WORKFLOW
                or run.get("head_repository", {}).get("full_name") != REPOSITORY
                or type(run.get("run_attempt")) is not int
                or run.get("run_attempt") != 1
                or not re.fullmatch(r"[a-f0-9]{40}", run.get("head_sha", ""))
            ):
                raise Refusal("stop_run_provenance_refused")
            if run.get("status") == "completed":
                conclusion = run.get("conclusion")
                if conclusion != "success":
                    if conclusion in {
                        "failure",
                        "cancelled",
                        "timed_out",
                        "skipped",
                        "action_required",
                        "neutral",
                        "stale",
                        "startup_failure",
                    }:
                        report["stop_run_conclusion"] = conclusion
                    raise Refusal("stop_run_failed")
                report.update(
                    status="passed",
                    stop_verified=True,
                    stop_run_conclusion="success",
                    stop_run_head_sha=run["head_sha"],
                )
                return report
            if run.get("status") not in {
                "queued",
                "in_progress",
                "waiting",
                "pending",
                "requested",
            }:
                raise Refusal("stop_run_state_refused")
            pause(min(5, max(0, deadline - clock())))
        raise Refusal("stop_run_timeout")
    except Exception as error:
        report["reason"] = str(error) if isinstance(error, Refusal) else "stop_verification_refused"
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = stop_scope(now=datetime.now(UTC), context=dict(os.environ))
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report))
    return 0 if report["stop_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
