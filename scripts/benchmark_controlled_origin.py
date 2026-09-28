"""Manually probe a bounded, cache-key-separated cohort of public forward reads.

Unique opaque query values seek fresh edge MISS responses without changing the
underlying read-only API. This is a diagnostic traffic model, not ordinary
visitor latency, a cold-start test, or proof that Postgres resumed from idle.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import Any
from urllib.request import build_opener

from scripts.benchmark_hosted_read_path import (
    _COMMIT_SHA,
    _MAX_BODY_BYTES,
    ROUTES,
    _percentile,
    _probe,
    _RejectRedirect,
    normalize_origin,
)

CONTROLLED_ROUTES = {
    name: ROUTES[name] for name in ("forward_status", "forecasts_page", "forward_performance")
}


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


def _fresh_origin(item: dict[str, Any]) -> bool:
    return (
        item.get("result") == "ok"
        and item.get("edge_cache") in {"MISS", "BYPASS"}
        and item.get("origin_timing_status") == "observed"
    )


def _route_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    observed = [item for item in items if _fresh_origin(item)]
    return {
        "requests": len(items),
        "fresh_origin_requests": len(observed),
        "result_counts": dict(sorted(Counter(item["result"] for item in items).items())),
        "edge_cache_counts": dict(
            sorted(Counter(str(item.get("edge_cache", "missing")) for item in items).items())
        ),
        "origin_timing_status_counts": dict(
            sorted(
                Counter(str(item.get("origin_timing_status", "missing")) for item in items).items()
            )
        ),
        "worker_state_counts": dict(
            sorted(Counter(str(item["worker_state"]) for item in observed).items())
        ),
        "fresh_origin_client_latency_ms": _latency(
            [float(item["latency_ms"]) for item in observed]
        ),
        "observed_app_header_ms": _latency([float(item["app_header_ms"]) for item in observed]),
        "observed_registry_read_ms": _latency(
            [float(item["registry_read_ms"]) for item in observed if "registry_read_ms" in item]
        ),
    }


def measure(
    origin: str,
    *,
    expected_commit_sha: str,
    samples_per_route: int = 5,
    pause_ms: int = 500,
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    """Send at most 26 sequential credential-free GETs, never retaining nonces."""
    base = normalize_origin(origin)
    if _COMMIT_SHA.fullmatch(expected_commit_sha) is None:
        raise ValueError("expected_commit_sha must be a lowercase 40-character Git SHA")
    if isinstance(samples_per_route, bool) or not 5 <= samples_per_route <= 8:
        raise ValueError("samples_per_route must be between 5 and 8")
    if isinstance(pause_ms, bool) or not 500 <= pause_ms <= 2000:
        raise ValueError("pause_ms must be between 500 and 2000")
    if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 15:
        raise ValueError("timeout_seconds must be finite and between 1 and 15")

    opener = build_opener(_RejectRedirect())
    before = _probe(
        opener, base + ROUTES["health"], timeout_seconds=timeout_seconds, route="health"
    )
    samples: dict[str, list[dict[str, Any]]] = {name: [] for name in CONTROLLED_ROUTES}
    for _ in range(samples_per_route):
        for route, path in CONTROLLED_ROUTES.items():
            separator = "&" if "?" in path else "?"
            url = f"{base}{path}{separator}observer_probe={token_hex(12)}"
            samples[route].append(_probe(opener, url, timeout_seconds=timeout_seconds, route=route))
            time.sleep(pause_ms / 1000)
    after = _probe(opener, base + ROUTES["health"], timeout_seconds=timeout_seconds, route="health")
    before_matches = (
        before.get("result") == "ok" and before.get("served_commit_sha") == expected_commit_sha
    )
    after_matches = (
        after.get("result") == "ok" and after.get("served_commit_sha") == expected_commit_sha
    )
    results = {route: _route_summary(items) for route, items in samples.items()}
    complete = (
        before_matches
        and after_matches
        and all(result["fresh_origin_requests"] == samples_per_route for result in results.values())
    )
    return {
        "schema_version": 1,
        "scope": "controlled_origin_cache_key_probe",
        "status": "observed" if complete else "degraded",
        "captured_at": datetime.now(UTC).isoformat(),
        "origin": base,
        "expected_commit_sha": expected_commit_sha,
        "traffic": {
            "request_order": "round_robin",
            "concurrency": 1,
            "health_checks": 2,
            "samples_per_route": samples_per_route,
            "pause_ms_between_requests": pause_ms,
            "timeout_seconds": timeout_seconds,
            "cache_key_parameter": "observer_probe",
            "nonce_retained": False,
            "max_response_bytes": _MAX_BODY_BYTES,
        },
        "health_identity": {
            "before_result": before.get("result"),
            "before_commit_matched": before_matches,
            "after_result": after.get("result"),
            "after_commit_matched": after_matches,
        },
        "results": results,
        "sample_observations": samples,
        "limitations": [
            "Unique-query traffic is a synthetic cache-key diagnostic, not ordinary visitor traffic.",
            "Only MISS/BYPASS responses with validated timing count as fresh-origin observations; an edge HIT fails this diagnostic.",
            "The first app-process marker cannot independently prove a platform cold start.",
            "Registry-read time combines connection acquisition, SQL, and Python; it cannot isolate an idle database resume.",
            "Client latency also includes network, edge, platform queueing, and response transfer; app-header time does not cover them all.",
            "This small sequential sample is not a capacity test, seven-day baseline, or SLO.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Public HTTPS origin; HTTP is accepted only for loopback")
    parser.add_argument("--expect-commit", required=True)
    parser.add_argument("--samples-per-route", type=int, default=5)
    parser.add_argument("--pause-ms", type=int, default=500)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = measure(
        args.origin,
        expected_commit_sha=args.expect_commit,
        samples_per_route=args.samples_per_route,
        pause_ms=args.pause_ms,
        timeout_seconds=args.timeout_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Wrote owner-only controlled-origin observation to {args.output}")
    return 0 if report["status"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
