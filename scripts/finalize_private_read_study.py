"""Collect body-free scheduled receipts after the fixed week; no provider reads.

Uses only the current public repository's read-only Actions credential. Manual
setup runs are excluded by event, and missing/failed/duplicate hourly slots are
reported rather than replayed. This command never closes an issue or sets an SLO.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import stat
import subprocess
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPOSITORY = "hoangnguyen2003/edgar-moe"
WORKFLOW = "observe-private-read-study.yml"
ROOT = Path(__file__).resolve().parents[1]
BASELINE = (
    ROOT
    / "ops/private-read-study/evidence/performance-preparation-20261106/prepared-local-baseline.json"
)
SETUP_EVIDENCE = ROOT / "ops/private-read-study/evidence/activated-study-20261106"
ROUTES = ("status", "forecasts", "ticker-filter", "performance")
FILES = {
    "observation.json",
    "correlated-observation.json",
    "neon-before.json",
    "neon-after.json",
    "SHA256SUMS",
}


class Refusal(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def window(env: dict[str, str]) -> tuple[datetime, datetime]:
    try:
        start = datetime.fromisoformat(env["STUDY_STARTS_AT"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(env["STUDY_EXPIRES_AT"].replace("Z", "+00:00"))
        if (
            env.get("GITHUB_REPOSITORY", REPOSITORY) != REPOSITORY
            or start.utcoffset() != timedelta(0)
            or end.utcoffset() != timedelta(0)
            or end - start != timedelta(days=7)
            or any(
                not re.fullmatch(r"[a-f0-9]{64}", env[key])
                for key in ("STUDY_POLICY_SHA256", "STUDY_DEPLOYMENT_HOST_SHA256")
            )
        ):
            raise ValueError()
        return start, end
    except Exception:
        raise Refusal("finalization_configuration_refused") from None


def gh(*args: str) -> bytes:
    run = subprocess.run(["gh", *args], capture_output=True, timeout=60)
    if run.returncode != 0:
        raise Refusal("actions_metadata_or_download_refused")
    return run.stdout


def archive_files(raw: bytes) -> dict[str, bytes]:
    """Validate a tiny flat receipt archive without extracting paths or links."""
    if len(raw) > 4_194_304:
        raise Refusal("artifact_size_refused")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [entry.filename for entry in archive.infolist()]
        if len(names) != len(set(names)) or set(names) - FILES or "SHA256SUMS" not in names:
            raise Refusal("artifact_inventory_refused")
        if any(
            entry.file_size > 1_048_576 or stat.S_ISLNK(entry.external_attr >> 16)
            for entry in archive.infolist()
        ):
            raise Refusal("artifact_member_refused")
        files = {name: archive.read(name) for name in names}
    expected = {}
    for line in files["SHA256SUMS"].decode().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  reports/private-read-study/([a-z-]+\.json)", line)
        if not match or match[2] in expected:
            raise Refusal("artifact_manifest_refused")
        expected[match[2]] = match[1]
    if set(expected) != set(files) - {"SHA256SUMS"} or any(
        hashlib.sha256(files[name]).hexdigest() != digest for name, digest in expected.items()
    ):
        raise Refusal("artifact_digest_refused")
    return files


def quantiles(values: list[int]) -> dict[str, int] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {"n": len(values)} | {
        name: ordered[math.ceil(fraction * len(values)) - 1]
        for name, fraction in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
    }


def summarize(
    records: list[dict[str, Any]], env: dict[str, str], now: datetime, baseline: dict[str, Any]
) -> dict[str, Any]:
    start, end = window(env)
    slots: dict[int, list[int]] = defaultdict(list)
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    failures: Counter[str] = Counter()
    dates: set[str] = set()
    attempts = errors = 0
    source_commits: set[str] = set()
    edge_counts: Counter[str] = Counter()
    platform_counts: Counter[str] = Counter()
    database_counts: Counter[str] = Counter()
    trace_counts: Counter[str] = Counter()
    workflow_counts: Counter[str] = Counter()
    degraded_pairs = 0
    pair_starts: list[datetime] = []
    for record in records:
        meta, receipt = record["run"], record["receipt"]
        # Dispatch is setup-only, even if it occupies a valid numbered slot.
        if meta["event"] != "schedule":
            continue
        if (
            meta["head_branch"] != "main"
            or meta["run_attempt"] != 1
            or receipt.get("scope") != "separate_portfolio_study290_observation"
            or receipt.get("schema_version") != 2
            or receipt.get("host_sha256") != env["STUDY_DEPLOYMENT_HOST_SHA256"]
            or receipt.get("policy_sha256") != env["STUDY_POLICY_SHA256"]
            or receipt.get("response_bodies_retained") is not False
            or receipt.get("database_credentials_in_probe") is not False
        ):
            raise Refusal("observation_identity_or_provenance_refused")
        stamp = datetime.fromisoformat(receipt["observed_at"])
        slot = receipt["hourly_slot"]
        if (
            stamp.utcoffset() != timedelta(0)
            or not start <= stamp < end
            or type(slot) is not int
            or slot != int((stamp - start).total_seconds() // 3600)
        ):
            raise Refusal("observation_window_refused")
        slots[slot].append(meta["id"])
        pair_starts.append(stamp)
        degraded_pairs += receipt.get("status") == "degraded"
        conclusion = meta.get("conclusion")
        workflow_counts[
            conclusion
            if conclusion in {"success", "failure", "cancelled", "timed_out"}
            else "unobserved"
        ] += 1
        dates.add(stamp.date().isoformat())
        source_commits.add(meta["head_sha"])
        if len(receipt["samples"]) not in (1, 2):
            raise Refusal("sample_count_refused")
        if [x["phase"] for x in receipt["samples"]] != ["first", "paired"][
            : len(receipt["samples"])
        ]:
            raise Refusal("sample_phase_refused")
        database_counts[receipt.get("database_lifecycle", {}).get("cohort", "unobserved")] += 1
        for sample in receipt["samples"]:
            route = sample["route"]
            if route != ROUTES[slot % 4]:
                raise Refusal("rotating_route_refused")
            attempts += 1
            passed = sample["status"] == "passed"
            errors += not passed
            if not passed:
                failures[sample.get("reason", "unclassified")] += 1
            provider = sample.get("provider_trace", {})
            trace_status = provider.get("status")
            trace_counts[
                trace_status if trace_status in {"passed", "refused"} else "unobserved"
            ] += 1
            platform = sample["platform_start_type"]
            platform_counts[platform] += 1
            edge_counts[sample["edge_cache"]] += 1
            if platform not in {"cold", "warm", "unobserved"}:
                raise Refusal("platform_cohort_refused")
            if platform != "unobserved" and (
                provider.get("status") != "passed" or provider.get("classification") != platform
            ):
                raise Refusal("platform_attribution_refused")
            lifecycle = receipt.get("database_lifecycle", {})
            db = lifecycle.get("cohort", "unobserved")
            process = (sample.get("origin_timing") or {}).get("process", "unobserved")
            # Keep code, route, phase, edge, platform, DB state and process apart.
            key = (
                meta["head_sha"],
                route,
                sample["phase"],
                sample["edge_cache"],
                platform,
                db,
                process,
            )
            groups[key].append(sample)
    cohorts = []
    for group_key, samples in sorted(groups.items()):
        good = [sample for sample in samples if sample["status"] == "passed"]
        for sample in good:
            timings = sample["origin_timing"]
            if (
                type(sample.get("http_elapsed_us")) is not int
                or sample["http_elapsed_us"] < 0
                or not isinstance(timings, dict)
                or any(type(v) is not int or v < 0 for k, v in timings.items() if k != "process")
            ):
                raise Refusal("numeric_timing_refused")
        metrics = {"http_elapsed_us": quantiles([x["http_elapsed_us"] for x in good])}
        for metric in (
            "preparation_us",
            "observer_connect_us",
            "reader_connect_us",
            "integrity_us",
            "service_us",
            "snapshot_us",
            "lifecycle_us",
        ):
            metrics[metric] = quantiles([x["origin_timing"][metric] for x in good])
        cohorts.append(
            dict(
                zip(
                    (
                        "collector_commit",
                        "route",
                        "phase",
                        "edge_cache",
                        "platform",
                        "database_pair_cohort",
                        "process",
                    ),
                    group_key,
                    strict=True,
                )
            )
            | {
                "attempts": len(samples),
                "errors": len(samples) - len(good),
                "quantiles_us": metrics,
            }
        )
    missing = sorted(set(range(168)) - set(slots))
    duplicates = {str(slot): ids for slot, ids in sorted(slots.items()) if len(ids) > 1}
    elapsed = now >= end
    # Completion here describes collection coverage, never automatic issue closure.
    complete = elapsed and not missing and not duplicates and attempts == 336
    return {
        "schema_version": 1,
        "scope": "portfolio_study290_final_report",
        "status": "coverage_complete" if complete else "incomplete",
        "starts_at": start.isoformat(),
        "expires_at": end.isoformat(),
        "generated_at": now.isoformat(),
        "window_hours": 168,
        "window_elapsed": elapsed,
        "observed_utc_date_count": len(dates),
        "first_pair_started_at": min(pair_starts).isoformat() if pair_starts else None,
        "last_pair_started_at": max(pair_starts).isoformat() if pair_starts else None,
        "pair_start_span_seconds": (max(pair_starts) - min(pair_starts)).total_seconds()
        if pair_starts
        else None,
        "host_sha256": env["STUDY_DEPLOYMENT_HOST_SHA256"],
        "policy_sha256": env["STUDY_POLICY_SHA256"],
        "expected_pairs": 168,
        "expected_measurement_requests": 336,
        "observed_pairs": sum(len(ids) for ids in slots.values()),
        "actual_http_attempts": attempts,
        "http_or_contract_errors": errors,
        "attempt_error_rate": errors / attempts if attempts else None,
        "failure_reasons": dict(failures),
        "platform_trace_status_counts": dict(trace_counts),
        "degraded_observation_pairs": degraded_pairs,
        "workflow_conclusion_counts": dict(workflow_counts),
        "edge_cache_counts": dict(edge_counts),
        "edge_hit_count": edge_counts["HIT"],
        "platform_counts": dict(platform_counts),
        "database_pair_counts": dict(database_counts),
        "acceptance_automatically_asserted": False,
        "missing_slots": missing,
        "duplicate_slots": duplicates,
        "utc_dates": sorted(dates),
        "collector_commits": sorted(source_commits),
        "manual_setup_runs_included": False,
        "application_requests_by_finalizer": 0,
        "percentile_method": "nearest_rank_successful_requests_only_with_n",
        "cohorts": cohorts,
        "local_native_reference": {
            "environment": baseline["environment"],
            "quantiles_us": baseline["quantiles_us"],
            "total_samples": baseline["total_native_read_samples"],
            "query_plan_shapes": len(baseline["local_select_only_query_plans"]),
        },
        "comparison_limits": [
            "Native local Unix-socket timings exclude HTTP, TLS and provider startup; never pool them with hosted latency.",
            "Database resume attribution is pair-level endpoint state, not an exact per-request resume timestamp.",
            "Private no-store routes do not intentionally exercise edge hits; report zero observed hits explicitly.",
            "Preparation CPU remains in HTTP/lifecycle time; bootstrap cache state is not separately reported.",
            "Small per-cohort tail samples are descriptive and do not justify an SLO or alert threshold.",
            "An elapsed policy window is not proof of seven days of observations; HTTP/contract errors exclude separate trace and workflow failures.",
        ],
        "slo_established": False,
        "issue_closed": False,
        "response_bodies_retained": False,
        "database_credentials_in_finalizer": False,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Issue 290: hosted read observation",
        "",
        f"Collection status: **{report['status']}**. Fixed window: {report['starts_at']} through {report['expires_at']}.",
        "",
        f"Observed {report['observed_pairs']}/168 scheduled pairs and {report['actual_http_attempts']}/336 modeled requests; {report['http_or_contract_errors']} HTTP/contract errors. Manual setup runs are excluded.",
        f"Actual observations cover {report['observed_utc_date_count']} UTC dates, from {report['first_pair_started_at']} through {report['last_pair_started_at']} (pair-start span: {report['pair_start_span_seconds']} seconds). The policy window elapsing does not fill missing observations.",
        f"Platform trace statuses: {report['platform_trace_status_counts']}; degraded pairs: {report['degraded_observation_pairs']}; workflow conclusions: {report['workflow_conclusion_counts']}. These are separate from HTTP/contract errors.",
        f"Missing slots: {report['missing_slots']}. Duplicate slots: {report['duplicate_slots']}.",
        "",
        "Successful hosted HTTP latency (milliseconds, nearest-rank; each row retains its own sample count):",
        "",
        "| Collector | Route | Phase | Edge | Provider | Database pair | Process | N | p50 | p95 | p99 | Errors |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["cohorts"]:
        q = row["quantiles_us"]["http_elapsed_us"]
        values = (
            [str(q["n"]), *(f"{q[key] / 1000:.3f}" for key in ("p50", "p95", "p99"))]
            if q
            else ["0", "—", "—", "—"]
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    row["collector_commit"][:7],
                    row["route"],
                    row["phase"],
                    row["edge_cache"],
                    row["platform"],
                    row["database_pair_cohort"],
                    row["process"],
                    *values,
                    str(row["errors"]),
                ]
            )
            + " |"
        )
    lines += [
        "",
        "The JSON retains preparation, connection, integrity, service, snapshot and lifecycle distributions separately. Preparation CPU is included in HTTP/lifecycle latency.",
        "",
        "Local native reference (milliseconds; Unix socket without HTTP/TLS, separate from the hosted rows):",
        "",
        "| Route | Service p50 | Service p95 | Service p99 | N |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for route, metrics in report["local_native_reference"]["quantiles_us"].items():
        q = metrics["service"]
        lines.append(
            f"| {route} | {q['p50'] / 1000:.3f} | {q['p95'] / 1000:.3f} | {q['p99'] / 1000:.3f} | {q['n']} |"
        )
    lines += [
        "",
        *("- " + limit for limit in report["comparison_limits"]),
        "",
        "No SLO/alert threshold is established and no issue is automatically closed. Review coverage, attribution, errors and the retained safe query plans before deciding whether the acceptance criteria are met.",
        "",
    ]
    return "\n".join(lines)


def collect(
    env: dict[str, str], destination: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    start, end = window(env)
    records, missing = [], []
    for page in range(1, 6):
        raw = gh(
            "api",
            "--method",
            "GET",
            f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs",
            "-f",
            "event=schedule",
            "-f",
            "branch=main",
            "-f",
            f"created={start.isoformat()}..{end.isoformat()}",
            "-f",
            "per_page=100",
            "-f",
            f"page={page}",
        )
        runs = json.loads(raw)["workflow_runs"]
        for run in runs:
            if (
                run["event"] != "schedule"
                or run["head_branch"] != "main"
                or run["run_attempt"] != 1
            ):
                raise Refusal("run_provenance_refused")
            name = f"private-read-study-{run['id']}-1"
            artifacts = json.loads(
                gh("api", f"repos/{REPOSITORY}/actions/runs/{run['id']}/artifacts?per_page=100")
            )["artifacts"]
            match = [a for a in artifacts if a["name"] == name and not a["expired"]]
            if len(match) != 1:
                missing.append(
                    {
                        "run_id": run["id"],
                        "conclusion": run["conclusion"],
                        "reason": "retained_artifact_missing",
                    }
                )
                continue
            files = archive_files(
                gh("api", f"repos/{REPOSITORY}/actions/artifacts/{match[0]['id']}/zip")
            )
            receipt = json.loads(
                files.get("correlated-observation.json", files["observation.json"])
            )
            directory = destination / str(run["id"])
            directory.mkdir(mode=0o700)
            for filename, contents in files.items():
                (directory / filename).write_bytes(contents)
            if receipt.get("scope") != "separate_portfolio_study290_observation":
                missing.append(
                    {
                        "run_id": run["id"],
                        "conclusion": run["conclusion"],
                        "reason": "observation_not_admitted",
                    }
                )
                continue
            meta = {
                key: run[key]
                for key in ("id", "event", "head_branch", "head_sha", "run_attempt", "conclusion")
            }
            records.append({"run": meta, "receipt": receipt})
        if len(runs) < 100:
            return records, missing
    raise Refusal("run_listing_bound_exceeded")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    env = dict(os.environ)
    try:
        _, end = window(env)
        now = datetime.now(UTC)
        if now < end + timedelta(minutes=5):
            print(json.dumps({"status": "collecting", "application_requests": 0}))
            return 0
        os.umask(0o077)
        args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        records, missing = collect(env, args.output_dir)
        report = summarize(records, env, now, json.loads(BASELINE.read_text()))
        report["runs_without_valid_observation"] = missing
        if missing:
            report["status"] = "incomplete"
        references = args.output_dir / "references"
        references.mkdir(mode=0o700)
        for source in (BASELINE, *sorted(SETUP_EVIDENCE.glob("*.json"))):
            if source.is_symlink():
                raise Refusal("reference_symlink_refused")
            (references / source.name).write_bytes(source.read_bytes())
        report["reference_sha256"] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in references.iterdir()
        }
        (args.output_dir / "summary.json").write_bytes(canonical(report) + b"\n")
        (args.output_dir / "summary.md").write_text(markdown(report))
        # The caller validates redaction before either upload or stop action.
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a") as stream:
                stream.write("finished=true\n")
        print(
            json.dumps(
                {"status": report["status"], "application_requests": 0, "issue_closed": False}
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "reason": str(error) if isinstance(error, Refusal) else "finalization_failed",
                    "values_withheld": True,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
