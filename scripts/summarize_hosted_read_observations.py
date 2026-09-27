"""Summarize redacted daily read observations without inventing provider attribution.

Inputs are the JSON artifacts from benchmark_hosted_read_path.py. A seven-day
coverage flag means seven consecutive UTC dates were sampled, not an SLO.
"""

from __future__ import annotations

import argparse
import json
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
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict) or report.get("schema_version") != 2:
            raise ValueError(f"{path}: expected hosted observation schema v2")
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
        if not isinstance(count, int) or not 5 <= count <= 20:
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
        if status not in {"observed", "warning", "degraded"}:
            raise ValueError(f"{path}: invalid report status")
        statuses[status] += 1
        if set(samples) != set(ROUTES):
            raise ValueError(f"{path}: route set changed")
        for route in ROUTES:
            route_items = samples[route]
            if not isinstance(route_items, list) or len(route_items) != count:
                raise ValueError(f"{path}: missing bounded samples for {route}")
            for item in route_items:
                if not isinstance(item, dict) or item.get("result") not in {
                    "ok",
                    "http_error",
                    "transport_error",
                    "contract_error",
                    "semantic_degraded",
                    "identity_mismatch",
                }:
                    raise ValueError(f"{path}: invalid sample for {route}")
                latency = item.get("latency_ms")
                if (
                    isinstance(latency, bool)
                    or not isinstance(latency, (int, float))
                    or not 0 <= latency < 1_000_000
                ):
                    raise ValueError(f"{path}: invalid latency for {route}")
                if (
                    route == "health"
                    and item["result"] == "ok"
                    and item.get("served_commit_sha") != commit
                ):
                    raise ValueError(f"{path}: health sample does not match expected commit")
                # Retain only fields used for aggregation; never copy input bodies.
                items_by_route[route].append(
                    {
                        "result": item["result"],
                        "latency_ms": float(latency),
                        "edge_cache": (
                            item.get("edge_cache")
                            if item.get("edge_cache") in ("HIT", "MISS", "STALE", "BYPASS", "other")
                            else "missing"
                        ),
                    }
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
        }
    longest = _longest_run(days)
    return {
        "schema_version": 1,
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
        "limitations": [
            "Daily samples are sequential and low-rate; coverage does not establish an SLO or concurrency capacity.",
            "Cache categories are observed response headers, not isolated edge, serverless, or database timing.",
            "Mixed serving commits are counted but not performance-equivalent cohorts.",
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
