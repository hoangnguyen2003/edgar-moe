"""Observe a small, sequential sample of the public read-only API.

This is an operator-initiated latency/error observation, not a load test,
throughput estimate, cache-control experiment, or production SLO. It never
sends credentials or records response bodies.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROUTES = {
    "health": "/api/v1/health",
    "summary": "/api/v1/summary",
    "forward_status": "/api/v1/forward/status",
    "forecasts_page": "/api/v1/forward/forecasts?limit=25",
}
_MAX_BODY_BYTES = 1024 * 1024
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class _RejectRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def normalize_origin(value: str) -> str:
    """Require an HTTPS origin; loopback HTTP is only for offline tests."""
    parsed = urlsplit(value.strip())
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname
        or parsed.scheme not in ({"http", "https"} if loopback else {"https"})
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("target must be a credential-free HTTPS origin (or HTTP loopback)")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _probe(opener: Any, url: str, *, timeout_seconds: float, route: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "edgar-moe-hosted-read-benchmark/1",
        },
    )
    started = time.perf_counter()
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            body = response.read(_MAX_BODY_BYTES + 1)
            status = int(response.status)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            cache = response.headers.get("X-Vercel-Cache", "missing").upper()
    except HTTPError as error:
        error.close()
        return {
            "result": "http_error",
            "http_status": int(error.code),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except (OSError, URLError, TimeoutError) as error:
        return {
            "result": "transport_error",
            "error_type": type(error).__name__,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    observation: dict[str, Any] = {
        "result": "ok",
        "http_status": status,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "edge_cache": cache if cache in {"HIT", "MISS", "STALE", "BYPASS"} else "other",
    }
    if status != 200 or content_type != "application/json" or len(body) > _MAX_BODY_BYTES:
        observation["result"] = "contract_error"
        return observation
    if route in {"health", "forward_status"}:
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            observation["result"] = "contract_error"
            return observation
        if not isinstance(payload, dict):
            observation["result"] = "contract_error"
        elif route == "health":
            observation["semantic_status"] = payload.get("status")
            commit = payload.get("commit_sha")
            if not isinstance(commit, str) or _COMMIT_SHA.fullmatch(commit) is None:
                observation["result"] = "contract_error"
            else:
                observation["served_commit_sha"] = commit
            if payload.get("status") != "ok" or payload.get("snapshot_loaded") is not True:
                observation["result"] = "semantic_degraded"
        else:
            observation["semantic_status"] = payload.get("health_status")
            observation["registry_available"] = payload.get("available")
            if payload.get("health_status") not in {"ok", "warning", "degraded"}:
                observation["result"] = "contract_error"
            elif payload.get("available") is not True or payload.get("health_status") == "degraded":
                observation["result"] = "semantic_degraded"
    return observation


def measure(
    origin: str,
    *,
    samples_per_route: int = 12,
    pause_ms: int = 250,
    timeout_seconds: float = 10,
    expected_commit_sha: str | None = None,
) -> dict[str, Any]:
    """Run one warmup and bounded round-robin samples with concurrency one."""
    base = normalize_origin(origin)
    if not 5 <= samples_per_route <= 20:
        raise ValueError("samples_per_route must be between 5 and 20")
    if not 200 <= pause_ms <= 2000:
        raise ValueError("pause_ms must be between 200 and 2000")
    if not 1 <= timeout_seconds <= 15 or not math.isfinite(timeout_seconds):
        raise ValueError("timeout_seconds must be finite and between 1 and 15")
    if expected_commit_sha is not None and _COMMIT_SHA.fullmatch(expected_commit_sha) is None:
        raise ValueError("expected_commit_sha must be a lowercase 40-character Git SHA")
    opener = build_opener(_RejectRedirect())
    warmup: dict[str, str] = {}
    observations: dict[str, list[dict[str, Any]]] = {name: [] for name in ROUTES}
    for name, path in ROUTES.items():
        item = _probe(opener, base + path, timeout_seconds=timeout_seconds, route=name)
        if (
            name == "health"
            and item["result"] == "ok"
            and expected_commit_sha is not None
            and item.get("served_commit_sha") != expected_commit_sha
        ):
            item["result"] = "identity_mismatch"
        warmup[name] = item["result"]
        time.sleep(pause_ms / 1000)
    for _ in range(samples_per_route):
        for name, path in ROUTES.items():
            item = _probe(opener, base + path, timeout_seconds=timeout_seconds, route=name)
            if (
                name == "health"
                and item["result"] == "ok"
                and expected_commit_sha is not None
                and item.get("served_commit_sha") != expected_commit_sha
            ):
                item["result"] = "identity_mismatch"
            observations[name].append(item)
            time.sleep(pause_ms / 1000)
    results: dict[str, Any] = {}
    for name, items in observations.items():
        successes = [float(item["latency_ms"]) for item in items if item["result"] == "ok"]
        results[name] = {
            "requests": len(items),
            "result_counts": dict(sorted(Counter(item["result"] for item in items).items())),
            "error_type_counts": dict(
                sorted(
                    Counter(item["error_type"] for item in items if "error_type" in item).items()
                )
            ),
            "http_status_counts": dict(
                sorted(
                    Counter(
                        str(item["http_status"]) for item in items if "http_status" in item
                    ).items()
                )
            ),
            "edge_cache_counts": dict(
                sorted(
                    Counter(item["edge_cache"] for item in items if "edge_cache" in item).items()
                )
            ),
            "successful_latency_ms": (
                {
                    "p50": round(statistics.median(successes), 3),
                    "p95": round(_percentile(successes, 0.95), 3),
                    "p99": round(_percentile(successes, 0.99), 3),
                    "max": round(max(successes), 3),
                }
                if successes
                else None
            ),
        }
        if name in {"health", "forward_status"}:
            results[name]["semantic_status_counts"] = dict(
                sorted(
                    Counter(
                        str(item["semantic_status"]) for item in items if "semantic_status" in item
                    ).items()
                )
            )
        if name == "health":
            results[name]["served_commit_sha_counts"] = dict(
                sorted(
                    Counter(
                        item["served_commit_sha"] for item in items if "served_commit_sha" in item
                    ).items()
                )
            )
        if name == "forward_status":
            results[name]["registry_available_counts"] = dict(
                sorted(
                    Counter(
                        str(item["registry_available"])
                        for item in items
                        if "registry_available" in item
                    ).items()
                )
            )
    healthy = all(result == "ok" for result in warmup.values()) and all(
        item["result_counts"] == {"ok": samples_per_route} for item in results.values()
    )
    warning = results["forward_status"]["semantic_status_counts"].get("warning", 0) > 0
    return {
        "schema_version": 1,
        "scope": "hosted_public_api_sequential_read_only_observation",
        "status": "degraded" if not healthy else "warning" if warning else "observed",
        "captured_at": datetime.now(UTC).isoformat(),
        "origin": base,
        "traffic": {
            "request_order": "round_robin",
            "concurrency": 1,
            "warmup_per_route": 1,
            "samples_per_route": samples_per_route,
            "pause_ms_between_requests": pause_ms,
            "timeout_seconds": timeout_seconds,
            "expected_commit_sha": expected_commit_sha,
            "max_response_bytes": _MAX_BODY_BYTES,
        },
        "warmup_result": warmup,
        "results": results,
        "limitations": [
            "Client-observed latency includes network, edge, serverless, and origin effects; no layer is isolated.",
            "A short sequential sample does not establish concurrency capacity, a seven-day baseline, or an SLO.",
            "Only successful 200 JSON responses enter latency percentiles; all failures are counted separately.",
            "No provider payload, credentials, or response bodies are retained.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Public HTTPS origin; HTTP is accepted only for loopback")
    parser.add_argument("--samples-per-route", type=int, default=12)
    parser.add_argument("--pause-ms", type=int, default=250)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument(
        "--expect-commit", help="Require every health sample to serve this exact Git SHA"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("refusing to overwrite an existing observation")
    report = measure(
        args.origin,
        samples_per_route=args.samples_per_route,
        pause_ms=args.pause_ms,
        timeout_seconds=args.timeout_seconds,
        expected_commit_sha=args.expect_commit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote bounded hosted read observation to {args.output}")
    return 0 if report["status"] in {"observed", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
