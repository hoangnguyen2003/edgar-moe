"""Evaluate real finite #156 receipts; never invoke providers or replay cycles."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
import stat
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from private_scheduler_scope import DATES, SCOPE_ID, SCOPE_SHA256, read_scope

REPO = "hoangnguyen2003/edgar-moe"
WORKFLOW = "forward-private-scheduler.yml"
ROOT = Path(__file__).resolve().parents[1]


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_receipt_field")
        result[key] = value
    return result


def gh(*args: str) -> bytes:
    result = subprocess.run(["gh", *args], capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError("github_metadata_or_artifact_refused")
    return result.stdout


def receipt(raw: bytes) -> dict[str, Any]:
    if len(raw) > 131072:
        raise ValueError("receipt_archive_too_large")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if (
            {x.filename for x in entries} != {"receipt.json", "SHA256SUMS"}
            or len(entries) != 2
            or any(x.file_size > 65536 or stat.S_ISLNK(x.external_attr >> 16) for x in entries)
        ):
            raise ValueError("receipt_inventory_refused")
        payload = archive.read("receipt.json")
        manifest = archive.read("SHA256SUMS").decode().strip()
    if manifest != hashlib.sha256(payload).hexdigest() + "  reports/private-scheduler/receipt.json":
        raise ValueError("receipt_digest_refused")
    value = json.loads(payload, object_pairs_hook=unique)
    allowed = {
        "status",
        "scope_id",
        "scope_sha256",
        "scheduled_at",
        "source_cutoff_rule_unchanged",
        "filing_coverage_configuration_unchanged",
        "origin_signature_verified",
        "actual_forecasts_recorded",
        "forecast_margin_proven",
        "issue_complete",
        "cutoff",
        "forecast_run_id",
        "forecasts_inserted",
        "recorded_at_utc",
        "target_regular_open_utc",
        "pre_open_margin_seconds",
        "existing_records_unchanged",
        "private_evidence_sha256",
        "new_storage_upper_bound_bytes",
        "stage",
        "error_type",
    }
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("receipt_schema_refused")
    return value


def evaluate(records: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    observations: dict[str, list[dict[str, Any]]] = {day: [] for day in DATES}
    for record in records:
        run, r = record["run"], record["receipt"]
        if r.get("scope_id") != SCOPE_ID or r.get("scope_sha256") != SCOPE_SHA256:
            continue  # Preserve older scopes without pooling them into this one.
        scheduled = datetime.fromisoformat(r["scheduled_at"])
        if scheduled.utcoffset() != timedelta(0) or scheduled.isoformat() not in {
            f"{day}T07:17:00+00:00" for day in DATES
        }:
            raise ValueError("receipt_slot_refused")
        if (
            run["event"] != "workflow_dispatch"
            or run["head_branch"] != "main"
            or run["run_attempt"] != 1
            or run["head_repository"]["full_name"] != REPO
            or run["path"] != ".github/workflows/" + WORKFLOW
        ):
            raise ValueError("receipt_run_provenance_refused")
        created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if not scheduled <= created <= scheduled + timedelta(minutes=90) or created > now:
            raise ValueError("receipt_dispatch_time_refused")
        margin = r.get("pre_open_margin_seconds")
        positive = (
            run["conclusion"] == "success"
            and r.get("status") == "recorded"
            and all(
                r.get(k) is True
                for k in (
                    "origin_signature_verified",
                    "source_cutoff_rule_unchanged",
                    "filing_coverage_configuration_unchanged",
                    "actual_forecasts_recorded",
                    "forecast_margin_proven",
                    "existing_records_unchanged",
                )
            )
            and type(r.get("forecasts_inserted")) is int
            and r["forecasts_inserted"] > 0
            and type(margin) in (int, float)
            and math.isfinite(margin)
            and margin >= 5400
            and type(r.get("new_storage_upper_bound_bytes")) is int
            and 0 <= r["new_storage_upper_bound_bytes"] <= 1_000_000_000
            and isinstance(r.get("private_evidence_sha256"), str)
            and re.fullmatch(r"[a-f0-9]{64}", r["private_evidence_sha256"]) is not None
            and r.get("cutoff")
            == scheduled.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        )
        if positive:
            recorded = datetime.fromisoformat(r["recorded_at_utc"])
            opening = datetime.fromisoformat(r["target_regular_open_utc"])
            if (
                recorded.utcoffset() != timedelta(0)
                or opening.utcoffset() != timedelta(0)
                or opening.astimezone(ZoneInfo("America/New_York")).weekday() >= 5
                or opening.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M:%S")
                != "09:30:00"
                or not created <= recorded <= min(now, scheduled + timedelta(hours=3))
                or abs((opening - recorded).total_seconds() - margin) > 1
            ):
                raise ValueError("recorded_margin_consistency_refused")
        observations[scheduled.date().isoformat()].append(
            {
                "run_id": run["id"],
                "run_url": f"https://github.com/{REPO}/actions/runs/{run['id']}",
                "source_commit": run["head_sha"],
                "scheduled_at": scheduled.isoformat(),
                "qualifying": positive,
                "pre_open_margin_seconds": margin
                if type(margin) in (int, float) and math.isfinite(margin)
                else None,
                "forecasts_inserted": r.get("forecasts_inserted")
                if type(r.get("forecasts_inserted")) is int
                else None,
                "private_evidence_sha256": r.get("private_evidence_sha256") if positive else None,
                "receipt_sha256": hashlib.sha256(
                    json.dumps(r, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                ).hexdigest(),
            }
        )
    eligible = all(len(rows) == 1 and rows[0]["qualifying"] for rows in observations.values())
    missing = [day for day, rows in observations.items() if not rows]
    duplicates = [day for day, rows in observations.items() if len(rows) > 1]
    expired = now >= datetime.fromisoformat(DATES[-1] + "T07:17:00+00:00") + timedelta(days=1)
    return {
        "issue": 156,
        "scope_id": SCOPE_ID,
        "scope_sha256": SCOPE_SHA256,
        "status": "acceptance_met" if eligible else "incomplete",
        "eligible_to_close": eligible,
        "observations": observations,
        "missing_dates": missing,
        "duplicate_dates": duplicates,
        "scope_finished": expired or not missing,
        "source_cutoff_rule_changed": False,
        "filing_coverage_configuration_changed": False,
        "minimum_pre_open_margin_seconds": 5400,
        "provider_requests": 0,
        "forecast_or_database_requests": 0,
        "billing_changes": 0,
        "evaluated_at": now.isoformat(),
    }


def closeout_body(report: dict[str, Any]) -> str:
    if report["eligible_to_close"] is not True:
        raise ValueError("acceptance_not_met")
    lines = [
        "Verified the two original acceptance criteria using three real, signed Cloudflare-scheduled private frozen-v1 cycles:",
        "",
        "| Scheduled UTC | Forecasts recorded | Pre-open margin (seconds) | Evidence |",
        "| --- | ---: | ---: | --- |",
    ]
    for rows in report["observations"].values():
        row = rows[0]
        lines.append(
            f"| {row['scheduled_at']} | {row['forecasts_inserted']} | {row['pre_open_margin_seconds']:.1f} | [run {row['run_id']}]({row['run_url']}) |"
        )
    lines += [
        "",
        "The Cloudflare weekday dialect is corrected to `17 7 * * TUE-SAT`, matching GitHub's original Tuesday–Saturday `17 7 * * 2-6`. Nominal UTC time, New York cutoff, source universe, lookback, frozen model and filing-coverage configuration are unchanged. Every qualifying run recorded nonzero forecasts with at least 90 minutes before entry, preserved existing rows, and retained its private evidence archive by hash.",
        "",
        "This is bounded operational validation for the personal portfolio project, not a statistical scheduler SLO or a general source-use/publication clearance. The general production source hold and frozen/append-only evidence remain intact. The finite provider-date guard prevents further source-backed cycles outside this scope.",
        "",
        f"Scope SHA-256: `{SCOPE_SHA256}`. Redacted aggregate report and original receipt hashes are retained in this closeout workflow's artifact.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if os.environ.get("GITHUB_REPOSITORY", REPO) != REPO:
            raise ValueError("repository_refused")
        read_scope(ROOT)
        records = []
        runs = json.loads(
            gh("api", f"repos/{REPO}/actions/workflows/{WORKFLOW}/runs?branch=main&per_page=100")
        )["workflow_runs"]
        for run in runs:
            if (
                run["status"] != "completed"
                or run["run_attempt"] != 1
                or run["event"] != "workflow_dispatch"
                or run["head_branch"] != "main"
            ):
                continue
            if datetime.fromisoformat(
                run["created_at"].replace("Z", "+00:00")
            ) < datetime.fromisoformat(DATES[0] + "T07:17:00+00:00"):
                continue
            name = f"private-scheduler-{run['id']}-1"
            artifacts = json.loads(
                gh("api", f"repos/{REPO}/actions/runs/{run['id']}/artifacts?per_page=100")
            )["artifacts"]
            match = [a for a in artifacts if a["name"] == name and not a["expired"]]
            if len(match) != 1:
                continue
            r = receipt(gh("api", f"repos/{REPO}/actions/artifacts/{match[0]['id']}/zip"))
            records.append({"run": run, "receipt": r})
        report = evaluate(records, datetime.now(UTC))
        os.umask(0o077)
        args.output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        for record in records:
            if record["receipt"].get("scope_id") == SCOPE_ID:
                (args.output_dir / f"receipt-{record['run']['id']}.json").write_text(
                    json.dumps(record["receipt"], indent=2) + "\n"
                )
        body = closeout_body(report) if report["eligible_to_close"] else ""
        if body:
            (args.output_dir / "closeout.md").write_text(body)
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
                stream.write(
                    f"eligible={'true' if report['eligible_to_close'] else 'false'}\nfinished={'true' if report['scope_finished'] else 'false'}\n"
                )
                if body:
                    stream.write(
                        "body="
                        + base64.b64encode(body.encode()).decode()
                        + "\nbody_sha256="
                        + hashlib.sha256(body.encode()).hexdigest()
                        + "\n"
                    )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "qualifying_observations": sum(
                        x["qualifying"] for rows in report["observations"].values() for x in rows
                    ),
                    "provider_requests": 0,
                }
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {"status": "refused", "error_type": type(error).__name__, "values_withheld": True}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
