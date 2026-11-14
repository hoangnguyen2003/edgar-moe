"""Retain one signed delivery-only receipt, or require its exact Actions proof.

No market/database/storage client exists here. A forecast must verify the
separate scheduled probe before installing its provider runtime. Missing,
duplicate, foreign, stale, failed or altered evidence refuses admission.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from private_scheduler_scope import (
    PROBE_DATE,
    SCOPE_ID,
    SCOPE_SHA256,
    WORKFLOW,
    admit,
    read_scope,
)

ROOT = Path(__file__).resolve().parents[1]
REPO = "hoangnguyen2003/edgar-moe"
PROBE_TIME = datetime.fromisoformat(PROBE_DATE + "T07:17:00+00:00")
ZERO_FIELDS = (
    "provider_requests",
    "database_connections",
    "storage_connections",
    "forecasts_invoked",
)
SOURCE_CREDENTIALS = (
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "SEC_USER_AGENT",
    "EDGAR_MOE_REGISTRY_DATABASE_URL",
    "EDGAR_MOE_R2_ENDPOINT_URL",
    "EDGAR_MOE_R2_BUCKET",
    "EDGAR_MOE_R2_ACCESS_KEY_ID",
    "EDGAR_MOE_R2_SECRET_ACCESS_KEY",
)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_delivery_field")
        result[key] = value
    return result


def stamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() != timedelta(0):
        raise ValueError("delivery_time_not_UTC")
    return result


def record_probe(inputs: dict[str, Any], context: dict[str, str], now: datetime) -> dict[str, Any]:
    record = read_scope(ROOT)
    if (
        inputs.get("mode") != "delivery_probe"
        or context.get("GITHUB_REPOSITORY") != REPO
        or context.get("GITHUB_RUN_ATTEMPT") != "1"
        or re.fullmatch(r"[a-f0-9]{40}", context.get("GITHUB_SHA", "")) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", context.get("GITHUB_RUN_ID", "")) is None
        or any(context.get(key) for key in SOURCE_CREDENTIALS)
    ):
        raise ValueError("delivery_probe_context_refused")
    scheduled = admit(
        inputs,
        key=context.get("SCHEDULER_SIGNATURE_KEY", ""),
        now=now,
        event=context.get("GITHUB_EVENT_NAME", ""),
        ref=context.get("GITHUB_REF", ""),
        allow_delivery_probe=True,
    )
    return {
        "schema_version": 1,
        "status": "passed",
        "mode": "delivery_probe",
        "scope_id": SCOPE_ID,
        "scope_sha256": SCOPE_SHA256,
        "scheduled_at": scheduled.isoformat(),
        "recorded_at_utc": now.isoformat(),
        "run_id": int(context["GITHUB_RUN_ID"]),
        "run_attempt": 1,
        "source_commit": context["GITHUB_SHA"],
        "origin_signature_verified": True,
        "runtime_inputs_sha256": hashlib.sha256(
            canonical(record["bound_input_sha256"])
        ).hexdigest(),
        **{key: 0 for key in ZERO_FIELDS},
        "forecast_margin_proven": False,
        "issue_complete": False,
    }


def validate_probe(run: dict[str, Any], proof: dict[str, Any], now: datetime) -> dict[str, Any]:
    record = read_scope(ROOT)
    if not isinstance(run, dict) or not isinstance(proof, dict):
        raise ValueError("delivery_proof_binding_refused")
    fields = {
        "schema_version",
        "status",
        "mode",
        "scope_id",
        "scope_sha256",
        "scheduled_at",
        "recorded_at_utc",
        "run_id",
        "run_attempt",
        "source_commit",
        "origin_signature_verified",
        "runtime_inputs_sha256",
        "forecast_margin_proven",
        "issue_complete",
        *ZERO_FIELDS,
    }
    if (
        set(proof) != fields
        or type(proof.get("schema_version")) is not int
        or proof["schema_version"] != 1
        or proof.get("status") != "passed"
        or proof.get("mode") != "delivery_probe"
        or proof.get("scope_id") != SCOPE_ID
        or proof.get("scope_sha256") != SCOPE_SHA256
        or proof.get("origin_signature_verified") is not True
        or proof.get("forecast_margin_proven") is not False
        or proof.get("issue_complete") is not False
        or any(type(proof.get(key)) is not int or proof[key] != 0 for key in ZERO_FIELDS)
        or type(proof.get("run_id")) is not int
        or proof["run_id"] <= 0
        or proof["run_id"] != run.get("id")
        or type(run.get("id")) is not int
        or type(proof.get("run_attempt")) is not int
        or proof["run_attempt"] != 1
        or type(run.get("run_attempt")) is not int
        or run["run_attempt"] != 1
        or run.get("event") != "workflow_dispatch"
        or run.get("head_branch") != "main"
        or run.get("path") != ".github/workflows/" + WORKFLOW
        or run.get("head_repository", {}).get("full_name") != REPO
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or re.fullmatch(r"[a-f0-9]{40}", run.get("head_sha", "")) is None
        or proof.get("source_commit") != run["head_sha"]
        or proof.get("runtime_inputs_sha256")
        != hashlib.sha256(canonical(record["bound_input_sha256"])).hexdigest()
    ):
        raise ValueError("delivery_proof_binding_refused")
    scheduled, created, recorded = (
        stamp(v) for v in (proof["scheduled_at"], run["created_at"], proof["recorded_at_utc"])
    )
    if (
        now.utcoffset() is None
        or scheduled != PROBE_TIME
        or not scheduled <= created <= recorded <= min(now, scheduled + timedelta(minutes=90))
    ):
        raise ValueError("delivery_proof_clock_refused")
    return {
        "status": "passed",
        "scope_id": SCOPE_ID,
        "scope_sha256": SCOPE_SHA256,
        "probe_run_id": run["id"],
        "probe_source_commit": run["head_sha"],
        "receipt_sha256": hashlib.sha256(canonical(proof)).hexdigest(),
        "provider_requests": 0,
        "forecasts_invoked": 0,
        "forecast_margin_proven": False,
    }


def parse_archive(raw: bytes) -> dict[str, Any]:
    if len(raw) > 131072:
        raise ValueError("delivery_archive_size_refused")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if (
            len(entries) != 2
            or {e.filename for e in entries} != {"receipt.json", "SHA256SUMS"}
            or any(e.file_size > 65536 or stat.S_ISLNK(e.external_attr >> 16) for e in entries)
        ):
            raise ValueError("delivery_archive_inventory_refused")
        payload = archive.read("receipt.json")
        manifest = archive.read("SHA256SUMS").decode().strip()
    if (
        manifest
        != hashlib.sha256(payload).hexdigest() + "  reports/scheduler-delivery/receipt.json"
    ):
        raise ValueError("delivery_archive_digest_refused")
    proof = json.loads(payload, object_pairs_hook=unique)
    if not isinstance(proof, dict):
        raise ValueError("delivery_receipt_object_required")
    return proof


def gh(path: str, *, raw: bool = False) -> Any:
    run = subprocess.run(
        ["gh", "api", "--hostname", "github.com", "-H", "X-GitHub-Api-Version: 2026-03-10", path],
        capture_output=True,
        timeout=30,
    )
    if run.returncode or len(run.stdout) > 1024**2:
        raise ValueError("delivery_Actions_metadata_refused")
    return run.stdout if raw else json.loads(run.stdout, object_pairs_hook=unique)


def require_probe(now: datetime, request: Callable[..., Any] = gh) -> dict[str, Any]:
    read_scope(ROOT)
    runs = request(
        f"repos/{REPO}/actions/workflows/{WORKFLOW}/runs?branch=main&event=workflow_dispatch&per_page=20"
    )
    if (
        not isinstance(runs, dict)
        or not isinstance(runs.get("workflow_runs"), list)
        or len(runs["workflow_runs"]) > 20
    ):
        raise ValueError("delivery_run_inventory_refused")
    candidates = [
        r
        for r in runs["workflow_runs"]
        if isinstance(r, dict)
        and r.get("status") == "completed"
        and r.get("conclusion") == "success"
        and PROBE_TIME <= stamp(r["created_at"]) <= PROBE_TIME + timedelta(minutes=90)
    ]
    if len(candidates) != 1 or type(candidates[0].get("id")) is not int or candidates[0]["id"] <= 0:
        raise ValueError("missing_or_duplicate_delivery_probe")
    identifier = candidates[0]["id"]
    run = request(f"repos/{REPO}/actions/runs/{identifier}")
    data = request(f"repos/{REPO}/actions/runs/{identifier}/artifacts?per_page=100")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) > 100:
        raise ValueError("delivery_artifact_inventory_refused")
    matching = [a for a in artifacts if a.get("name") == f"scheduler-delivery-probe-{identifier}-1"]
    if (
        len(matching) != 1
        or matching[0].get("expired") is not False
        or type(matching[0].get("id")) is not int
        or matching[0]["id"] <= 0
        or type(matching[0].get("size_in_bytes")) is not int
        or not 0 < matching[0]["size_in_bytes"] <= 131072
    ):
        raise ValueError("delivery_artifact_binding_refused")
    raw = request(f"repos/{REPO}/actions/artifacts/{matching[0]['id']}/zip", raw=True)
    report = validate_probe(run, parse_archive(raw), now)
    return {**report, "actions_metadata_requests": 4}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", type=Path)
    mode.add_argument("--require", dest="require_proof", action="store_true")
    args = parser.parse_args()
    try:
        now = datetime.now(UTC)
        if args.record:
            event = json.loads(
                Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes(), object_pairs_hook=unique
            )
            report = record_probe(event.get("inputs", {}), dict(os.environ), now)
            os.umask(0o077)
            args.record.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with args.record.open("x") as stream:
                json.dump(report, stream, indent=2)
                stream.write("\n")
        else:
            if (
                os.environ.get("GITHUB_REPOSITORY") != REPO
                or os.environ.get("GITHUB_REF") != "refs/heads/main"
                or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
            ):
                raise ValueError("delivery_consumer_context_refused")
            report = require_probe(now)
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "status": "delivery_evidence_refused",
                    "provider_requests": 0,
                    "forecasts_invoked": 0,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
