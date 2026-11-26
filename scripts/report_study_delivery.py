"""Reconcile the fixed #290 delivery trial using only GitHub Actions read APIs."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any

from scripts.finalize_private_read_study import REPOSITORY, Refusal, gh
from scripts.observe_external_study_delivery import FORMAT

WORKFLOW = "study-external-delivery.yml"
SCOPE = "issue290_external_scheduler_delivery"
HOUR = timedelta(hours=1)
OFFSET = timedelta(minutes=17)
RUNS = f"repos/{REPOSITORY}/actions/runs"
LINE = re.compile(r"^\ufeff?\d{4}-\S+Z study290_external_delivery (\{[^\r\n]+\})$", re.MULTILINE)
COUNTERS = {"application_requests": 0, "provider_requests": 0, "measurement_pairs": 0}


def utc(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() != timedelta(0):
        raise ValueError("UTC timestamp required")
    return stamp


def trial_start(value: str) -> datetime:
    stamp = utc(value)
    if stamp.strftime(FORMAT) != value or stamp.minute or stamp.second:
        raise ValueError("Trial start must be an exact UTC hour with .000Z")
    return stamp


def parse_receipt(
    run: dict[str, Any], job: dict[str, Any], raw: bytes, start: datetime
) -> tuple[int, dict[str, Any]]:
    if len(raw) > 1_048_576:
        raise ValueError("Job log exceeds the receipt limit")
    lines = LINE.findall(raw.decode())
    if len(lines) != 1:
        raise ValueError("Expected one signed receipt")
    receipt = json.loads(lines[0])
    slot = receipt["hourly_slot"]
    if type(slot) is not int or not 0 <= slot < 24:
        raise ValueError("Invalid slot")
    scheduled = start + slot * HOUR + OFFSET
    created, started, observed = map(
        utc,
        (
            run["created_at"],
            job["started_at"],
            receipt["observed_at"],
        ),
    )
    if (
        receipt["scope"] != SCOPE
        or type(receipt["run_id"]) is not int
        or receipt["run_id"] != run["id"]
        or receipt["starts_at"] != start.strftime(FORMAT)
        or receipt["scheduled_at"] != scheduled.strftime(FORMAT)
        or any(type(receipt.get(key)) is not int or receipt[key] != 0 for key in COUNTERS)
        or not scheduled <= created <= started <= observed < start + (slot + 1) * HOUR
        or not re.fullmatch(r"[a-f0-9]{40}", run["head_sha"])
    ):
        raise ValueError("Receipt metadata mismatch")
    return slot, {
        "run_id": run["id"],
        "commit": run["head_sha"],
        "github_received_at": created.isoformat(),
        "job_started_at": started.isoformat(),
        "verified_at": observed.isoformat(),
        "scheduled_to_github_seconds": (created - scheduled).total_seconds(),
        "github_queue_seconds": (started - created).total_seconds(),
        "job_to_receipt_seconds": (observed - started).total_seconds(),
    }


def collect(start: datetime, now: datetime) -> dict[str, Any]:
    end = start + 24 * HOUR
    listing = json.loads(
        gh(
            "api",
            "--method",
            "GET",
            f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs",
            "-f",
            "event=workflow_dispatch",
            "-f",
            "branch=main",
            "-f",
            f"created={start.isoformat()}..{end.isoformat()}",
            "-f",
            "per_page=100",
        )
    )
    runs = listing["workflow_runs"]
    ids = [run["id"] for run in runs]
    if listing["total_count"] != len(runs) or len(runs) > 100 or len(set(ids)) != len(ids):
        raise Refusal("run_inventory_refused")
    receipts: list[list[dict[str, Any]]] = [[] for _ in range(24)]
    unverified = []
    setup_checks = 0
    for run in runs:
        run_id = run["id"]
        if (
            type(run_id) is not int
            or run_id <= 0
            or run["event"] != "workflow_dispatch"
            or run["head_branch"] != "main"
            or run["head_repository"]["full_name"] != REPOSITORY
        ):
            raise Refusal("run_provenance_refused")
        if not start <= utc(run["created_at"]) < min(end, now):
            continue
        if run["run_attempt"] != 1:
            unverified.append({"run_id": run_id, "reason": "rerun_excluded"})
            continue
        if run["status"] != "completed" or run["conclusion"] != "success":
            unverified.append({"run_id": run_id, "reason": "run_without_success"})
            continue
        jobs = json.loads(gh("api", f"{RUNS}/{run_id}/attempts/1/jobs?per_page=10"))
        if jobs["total_count"] != len(jobs["jobs"]) or len(jobs["jobs"]) > 10:
            raise Refusal("job_inventory_refused")
        by_name = {job["name"]: job for job in jobs["jobs"]}
        if len(by_name) != len(jobs["jobs"]):
            raise Refusal("job_provenance_refused")
        probe = by_name.get("probe")
        if (
            probe
            and probe["conclusion"] == "skipped"
            and (by_name.get("check-credentials", {}).get("conclusion") == "success")
        ):
            setup_checks += 1
            continue
        if not probe or probe["conclusion"] != "success":
            unverified.append({"run_id": run_id, "reason": "probe_without_success"})
            continue
        job_id = probe["id"]
        if type(job_id) is not int or job_id <= 0 or probe["run_id"] != run_id:
            raise Refusal("job_provenance_refused")
        try:
            slot, value = parse_receipt(
                run, probe, gh("api", f"repos/{REPOSITORY}/actions/jobs/{job_id}/logs"), start
            )
        except (KeyError, TypeError, ValueError):
            unverified.append({"run_id": run_id, "reason": "receipt_unverified"})
            continue
        receipts[slot].append(value)

    slots = []
    for index, observed in enumerate(receipts):
        if observed:
            state = "confirmed" if len(observed) == 1 else "duplicate"
        else:
            state = "missing" if now >= start + (index + 1) * HOUR else "pending"
        slots.append(
            {
                "hourly_slot": index,
                "scheduled_at": (start + index * HOUR + OFFSET).strftime(FORMAT),
                "status": state,
                "receipts": sorted(observed, key=lambda item: item["run_id"]),
            }
        )
    counts = {
        state: sum(slot["status"] == state for slot in slots)
        for state in ("confirmed", "duplicate", "missing", "pending")
    }
    return {
        "scope": "issue290_external_delivery_report",
        "starts_at": start.strftime(FORMAT),
        "expires_at": end.strftime(FORMAT),
        "as_of": now.isoformat(timespec="seconds"),
        "status": "collecting"
        if now < end
        else ("complete" if counts["confirmed"] == 24 and not unverified else "incomplete"),
        "slots": slots,
        "counts": counts,
        "setup_checks_excluded": setup_checks,
        "unverified_runs": sorted(unverified, key=lambda item: item["run_id"]),
        **COUNTERS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--starts-at", required=True, type=trial_start)
    args = parser.parse_args()
    try:
        report = collect(args.starts_at, datetime.now(UTC))
    except (OSError, subprocess.TimeoutExpired, KeyError, TypeError, ValueError):
        print(json.dumps({"status": "delivery_report_refused", **COUNTERS}))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
