"""Summarize redacted daily read observations without inventing provider attribution.

Inputs are the JSON artifacts from benchmark_hosted_read_path.py. A seven-day
coverage flag means seven consecutive UTC dates were sampled, not an SLO.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.benchmark_hosted_read_path import ROUTES, _percentile, normalize_origin


def _latency(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    return {
        "requests": len(values),
        "p50": round(statistics.median(values), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "p99": round(_percentile(values, 0.99), 3),
        "max": round(max(values), 3),
    }


def _day(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("captured_at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("captured_at must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("captured_at must include a UTC offset")
    return parsed.astimezone(UTC).date()


def _longest_run(days: set[date]) -> int:
    longest = current = 0
    previous: date | None = None
    for day in sorted(days):
        current = current + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        longest = max(longest, current)
        previous = day
    return longest


_RESULTS = {
    "ok",
    "http_error",
    "transport_error",
    "contract_error",
    "semantic_degraded",
    "identity_mismatch",
}


def _safe_sample(
    item: Any, *, path: Path, route: str, commit: str, schema_version: int
) -> dict[str, Any]:
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("result"), str)
        or item["result"] not in _RESULTS
    ):
        raise ValueError(f"{path}: invalid sample for {route}")
    latency = item.get("latency_ms")
    if (
        isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or not math.isfinite(latency)
        or not 0 <= latency < 1_000_000
    ):
        raise ValueError(f"{path}: invalid latency for {route}")
    if route == "health" and item["result"] == "ok" and item.get("served_commit_sha") != commit:
        raise ValueError(f"{path}: health sample does not match expected commit")
    cache = (
        item.get("edge_cache")
        if item.get("edge_cache") in ("HIT", "MISS", "STALE", "BYPASS", "other")
        else "missing"
    )
    # Never copy bodies, raw headers, or unexpected strings.
    safe_item: dict[str, Any] = {
        "result": item["result"],
        "latency_ms": float(latency),
        "edge_cache": cache,
        "origin_timing_status": "legacy_v2"
        if schema_version == 2
        else item.get("origin_timing_status", "missing"),
    }
    if schema_version == 3:
        timing_status = safe_item["origin_timing_status"]
        if not isinstance(timing_status, str) or timing_status not in {
            "observed",
            "missing",
            "invalid",
            "edge_or_unknown_ignored",
        }:
            raise ValueError(f"{path}: invalid origin timing status")
        if timing_status == "observed":
            if cache not in {"MISS", "BYPASS"}:
                raise ValueError(f"{path}: cached timing cannot be origin evidence")
            worker = item.get("worker_state")
            if not isinstance(worker, str) or worker not in {"first", "subsequent"}:
                raise ValueError(f"{path}: invalid worker state")
            safe_item["worker_state"] = worker
            for field in ("app_header_ms", "registry_read_ms"):
                value = item.get(field)
                if field == "registry_read_ms" and value is None:
                    continue
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or not 0 <= value <= 600_000
                ):
                    raise ValueError(f"{path}: invalid origin timing metric")
                safe_item[field] = float(value)
        elif any(field in item for field in ("worker_state", "app_header_ms", "registry_read_ms")):
            raise ValueError(f"{path}: unattributed origin timing fields")
    return safe_item


def summarize(paths: list[Path]) -> dict[str, Any]:
    if not paths:
        raise ValueError("at least one observation is required")
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate observation path")
    days: set[date] = set()
    origins: set[str] = set()
    commits: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    items_by_route: dict[str, list[dict[str, Any]]] = {name: [] for name in ROUTES}
    warmups_by_route: dict[str, list[dict[str, Any]]] = {name: [] for name in ROUTES}
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        schema_version = report.get("schema_version") if isinstance(report, dict) else None
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version not in {2, 3}
        ):
            raise ValueError(f"{path}: expected hosted observation schema v2 or v3")
        if report.get("scope") != "hosted_public_api_sequential_read_only_observation":
            raise ValueError(f"{path}: unexpected observation scope")
        days.add(_day(report.get("captured_at")))
        origin = report.get("origin")
        if not isinstance(origin, str):
            raise ValueError(f"{path}: missing origin")
        origins.add(normalize_origin(origin))
        traffic = report.get("traffic")
        samples = report.get("sample_observations")
        if not isinstance(traffic, dict) or not isinstance(samples, dict):
            raise ValueError(f"{path}: missing traffic or samples")
        count = traffic.get("samples_per_route")
        if isinstance(count, bool) or not isinstance(count, int) or not 5 <= count <= 20:
            raise ValueError(f"{path}: invalid bounded sample count")
        commit = traffic.get("expected_commit_sha")
        if (
            not isinstance(commit, str)
            or len(commit) != 40
            or any(char not in "0123456789abcdef" for char in commit)
        ):
            raise ValueError(f"{path}: expected serving commit is required")
        commits[commit] += 1
        status = report.get("status")
        if not isinstance(status, str) or status not in {"observed", "warning", "degraded"}:
            raise ValueError(f"{path}: invalid report status")
        statuses[status] += 1
        if set(samples) != set(ROUTES):
            raise ValueError(f"{path}: route set changed")
        if schema_version == 3:
            warmups = report.get("warmup_observations")
            if not isinstance(warmups, dict) or set(warmups) != set(ROUTES):
                raise ValueError(f"{path}: missing schema-v3 warmup observations")
            for route in ROUTES:
                warmups_by_route[route].append(
                    _safe_sample(
                        warmups[route],
                        path=path,
                        route=route,
                        commit=commit,
                        schema_version=schema_version,
                    )
                )
        for route in ROUTES:
            route_items = samples[route]
            if not isinstance(route_items, list) or len(route_items) != count:
                raise ValueError(f"{path}: missing bounded samples for {route}")
            for item in route_items:
                items_by_route[route].append(
                    _safe_sample(
                        item,
                        path=path,
                        route=route,
                        commit=commit,
                        schema_version=schema_version,
                    )
                )
    if len(origins) != 1:
        raise ValueError("observations from different origins cannot be pooled")
    results: dict[str, Any] = {}
    for route, items in items_by_route.items():
        successful = [item for item in items if item["result"] == "ok"]
        caches = sorted({str(item["edge_cache"]) for item in successful})
        results[route] = {
            "requests": len(items),
            "result_counts": dict(sorted(Counter(item["result"] for item in items).items())),
            "successful_latency_ms": _latency([item["latency_ms"] for item in successful]),
            "successful_latency_by_edge_cache_ms": {
                cache: _latency(
                    [item["latency_ms"] for item in successful if item["edge_cache"] == cache]
                )
                for cache in caches
            },
            "origin_timing_status_counts": dict(
                sorted(Counter(item["origin_timing_status"] for item in items).items())
            ),
            "successful_origin_latency_by_worker_ms": {
                worker: _latency(
                    [
                        item["latency_ms"]
                        for item in successful
                        if item["origin_timing_status"] == "observed"
                        and item.get("worker_state") == worker
                    ]
                )
                for worker in ("first", "subsequent")
                if any(
                    item["origin_timing_status"] == "observed"
                    and item.get("worker_state") == worker
                    for item in successful
                )
            },
            "observed_app_header_ms": _latency(
                [
                    item["app_header_ms"]
                    for item in successful
                    if item["origin_timing_status"] == "observed"
                ]
            ),
            "observed_registry_read_ms": _latency(
                [
                    item["registry_read_ms"]
                    for item in successful
                    if item["origin_timing_status"] == "observed" and "registry_read_ms" in item
                ]
            ),
        }
    warmup_results: dict[str, Any] = {}
    for route, items in warmups_by_route.items():
        successful = [item for item in items if item["result"] == "ok"]
        warmup_results[route] = {
            "requests": len(items),
            "result_counts": dict(sorted(Counter(item["result"] for item in items).items())),
            "origin_timing_status_counts": dict(
                sorted(Counter(item["origin_timing_status"] for item in items).items())
            ),
            "successful_client_latency_by_worker_ms": {
                worker: _latency(
                    [
                        item["latency_ms"]
                        for item in successful
                        if item["origin_timing_status"] == "observed"
                        and item.get("worker_state") == worker
                    ]
                )
                for worker in ("first", "subsequent")
                if any(
                    item["origin_timing_status"] == "observed"
                    and item.get("worker_state") == worker
                    for item in successful
                )
            },
        }
    longest = _longest_run(days)
    return {
        "schema_version": 2,
        "scope": "multi_day_hosted_public_api_client_observation",
        "origin": next(iter(origins)),
        "input_reports": len(paths),
        "distinct_utc_days": len(days),
        "longest_consecutive_utc_days": longest,
        "seven_day_coverage": longest >= 7,
        "first_utc_day": min(days).isoformat(),
        "last_utc_day": max(days).isoformat(),
        "expected_commit_report_counts": dict(sorted(commits.items())),
        "report_status_counts": dict(sorted(statuses.items())),
        "results": results,
        "warmup_results": warmup_results,
        "limitations": [
            "Daily samples are sequential and low-rate; coverage does not establish an SLO or concurrency capacity.",
            "One warmup per route is reported separately from measured requests; warmups from legacy v2 reports are unavailable.",
            "First/subsequent app-process markers on edge MISS/BYPASS can separate those samples, but do not prove platform cold starts or idle database resume.",
            "Registry-read duration includes connection acquisition, SQL, and Python computation; it is not database-only timing.",
            "Legacy v2 observations have no origin timing, and mixed serving commits are not performance-equivalent cohorts.",
            "Only successful responses enter latency percentiles; all other results remain counted.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("refusing to overwrite an existing summary")
    summary = summarize(args.reports)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"Summarized {summary['input_reports']} observations; seven-day coverage: {summary['seven_day_coverage']}"
    )
    return 0 if summary["seven_day_coverage"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
