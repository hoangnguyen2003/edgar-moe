"""Bounded authenticated hosted smoke, retaining no credentials or response rows.

Manual only: no deployment, scheduling, source access, mutation SQL or retries.
HTTP write methods test the service's method guard, not a database mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import httpx

from edgar_moe.api.private_read import TOKEN

TIMING = re.compile(r"read_ms=(\d+\.\d{3});process=(first|subsequent)\Z")
CASES = (
    ("anonymous_status", "GET", "/v1/status", False, 401),
    ("anonymous_forecasts", "GET", "/v1/forecasts", False, 401),
    ("anonymous_performance", "GET", "/v1/performance", False, 401),
    ("post_rejected", "POST", "/v1/status", True, 405),
    ("delete_rejected", "DELETE", "/v1/status", True, 405),
    ("put_rejected", "PUT", "/v1/status", True, 405),
    ("patch_rejected", "PATCH", "/v1/status", True, 405),
    ("docs_absent", "GET", "/docs", True, 404),
    ("unbounded_filter_rejected", "GET", "/v1/forecasts?limit=1000", True, 400),
    ("status", "GET", "/v1/status", True, 200),
    ("forecasts", "GET", "/v1/forecasts?limit=1", True, 200),
    ("filtered_forecasts", "GET", "/v1/forecasts?ticker=FIXT&limit=1", True, 200),
    ("empty_filter", "GET", "/v1/forecasts?ticker=NONE&limit=1", True, 200),
    ("performance", "GET", "/v1/performance", True, 200),
    ("status_repeat", "GET", "/v1/status", True, 200),
)


class ProbeRefusal(ValueError):
    pass


def validate_url(value: str) -> None:
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or not re.fullmatch(r"[a-z0-9-]+\.vercel\.app", url.hostname)
            or url.username
            or url.password
            or url.port
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError
    except ValueError:
        raise ProbeRefusal("invalid_private_pilot_origin") from None


def observe(base_url: str, token: str, client: httpx.Client) -> dict[str, Any]:
    validate_url(base_url)
    if not TOKEN.fullmatch(token):
        raise ProbeRefusal("invalid_token")
    observations: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "authenticated_synthetic_pilot_smoke",
        "observed_at": datetime.now(UTC).isoformat(),
        "status": "failed",
        "reason": None,
        "observations": observations,
        "limitations": [
            "not_representative_volume_or_seven_day_slo",
            "not_provider_managed_backup_rpo_rto",
            "per_process_not_global_connection_budget",
            "no_database_mutation_probes",
        ],
    }
    for name, method, path, authenticated, expected in CASES:
        headers = {"Authorization": f"Bearer {token}"} if authenticated else {}
        started = perf_counter()
        try:
            with client.stream(method, base_url.rstrip("/") + path, headers=headers) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 16384:
                        raise ProbeRefusal("response_too_large")
                payload = json.loads(body)
                if response.status_code != expected:
                    raise ProbeRefusal("unexpected_http_status")
                if (
                    any(
                        response.headers.get(header) != "private, no-store"
                        for header in ("Cache-Control", "CDN-Cache-Control")
                    )
                    or "access-control-allow-origin" in response.headers
                ):
                    raise ProbeRefusal("private_cache_boundary_failed")
                observed: dict[str, Any] = {
                    "case": name,
                    "status": response.status_code,
                    "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                }
                if expected == 200:
                    if (
                        not isinstance(payload, dict)
                        or payload.get("data_mode") != "synthetic_fixture"
                        or payload.get("research_only") is not True
                        or payload.get("read_policy", {}).get("transaction_read_only") is not True
                        or payload["read_policy"].get("statement_timeout_ms") != 3000
                        or payload["read_policy"].get("connection_max_per_process") != 1
                        or payload["read_policy"].get("persistent_connections") != 0
                        or payload["read_policy"].get("global_connection_cap_verified") is not False
                    ):
                        raise ProbeRefusal("read_policy_failed")
                    timing = TIMING.fullmatch(response.headers.get("X-Pilot-Read-Timing", ""))
                    if timing is None:
                        raise ProbeRefusal("timing_missing")
                    observed.update(read_ms=float(timing[1]), process=timing[2])
                    if name.startswith("status"):
                        counts = payload.get("counts", {})
                        if counts.get("forward_forecasts") != 1 or payload.get("failed_runs") != 1:
                            raise ProbeRefusal("synthetic_count_mismatch")
                        observed["server_max_connections"] = payload["read_policy"].get(
                            "server_max_connections"
                        )
                    if name in {"forecasts", "filtered_forecasts", "empty_filter"}:
                        total = 0 if name == "empty_filter" else 1
                        if payload.get("total") != total or len(payload.get("items", [])) != total:
                            raise ProbeRefusal("synthetic_filter_mismatch")
                    if name == "performance" and (
                        payload.get("forecast_count") != 1 or payload.get("matured_count") != 0
                    ):
                        raise ProbeRefusal("synthetic_performance_mismatch")
                observations.append(observed)
        except Exception as error:
            report["reason"] = str(error) if isinstance(error, ProbeRefusal) else "request_failed"
            return report
    report["status"] = "passed"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        # Bypass only the separate project's platform gate, not application auth.
        # Never place this credential in a query/shareable URL or retained output.
        bypass = os.environ.get("PILOT_VERCEL_BYPASS_TOKEN", "")
        headers = {"x-vercel-protection-bypass": bypass} if bypass else {}
        with httpx.Client(
            timeout=20, follow_redirects=False, trust_env=False, headers=headers
        ) as client:
            report = observe(
                os.environ.get("PILOT_BASE_URL", ""),
                os.environ.get("PILOT_BEARER_TOKEN", ""),
                client,
            )
    except Exception:
        report = {"schema_version": 1, "status": "failed", "reason": "configuration_invalid"}
    with os.fdopen(
        os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
