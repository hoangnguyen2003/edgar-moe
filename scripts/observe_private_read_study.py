"""Two bounded authenticated reads of one fixed synthetic study route.

No database credential, dotenv, provider payload retention or redirect following.
Process-first observations are not relabeled as provider cold starts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROUTES = ("status", "forecasts", "ticker-filter", "performance")
CACHE = {"HIT", "MISS", "BYPASS", "STALE", "PRERENDER", "REVALIDATED"}
TIMINGS = {
    "observer_connect_us",
    "reader_connect_us",
    "integrity_us",
    "service_us",
    "snapshot_us",
    "lifecycle_us",
}
MAX_BODY = 262144


class Refusal(ValueError):
    pass


@dataclass(frozen=True, repr=False)
class Configuration:
    origin: str
    bearer: str = field(repr=False)
    bypass: str = field(repr=False)
    host_sha256: str
    policy_sha256: str
    starts_at: datetime
    expires_at: datetime

    @classmethod
    def load(cls, env: dict[str, str]) -> Configuration:
        try:
            origin = env["STUDY_DEPLOYMENT_URL"]
            parsed = urlsplit(origin)
            start = datetime.fromisoformat(env["STUDY_STARTS_AT"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(env["STUDY_EXPIRES_AT"].replace("Z", "+00:00"))
            host_hash = env["STUDY_DEPLOYMENT_HOST_SHA256"]
            policy_hash = env["STUDY_POLICY_SHA256"]
            bearer, bypass = env["STUDY_BEARER_TOKEN"], env["STUDY_PLATFORM_BYPASS"]
            if (
                parsed.scheme != "https"
                or not re.fullmatch(r"[a-z0-9-]+\.vercel\.app", parsed.hostname or "")
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
                or start.utcoffset() != timedelta(0)
                or end.utcoffset() != timedelta(0)
                or start.minute
                or start.second
                or start.microsecond
                or end - start != timedelta(days=7)
                or not re.fullmatch(r"[a-f0-9]{64}", host_hash)
                or hashlib.sha256((parsed.hostname or "").encode()).hexdigest() != host_hash
                or not re.fullmatch(r"[a-f0-9]{64}", policy_hash)
                or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", bearer)
                or not 20 <= len(bypass) <= 512
                or any(c.isspace() for c in bypass)
            ):
                raise ValueError()
            return cls(origin.rstrip("/"), bearer, bypass, host_hash, policy_hash, start, end)
        except Exception:
            raise Refusal("study_configuration_refused") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def timing_header(value: str | None, cache: str) -> dict[str, Any] | None:
    if cache not in {"MISS", "BYPASS"} or not value or len(value) > 512:
        return None
    result: dict[str, Any] = {}
    for part in value.split(";"):
        if part.count("=") != 1:
            return None
        key, raw = part.split("=")
        if key in result:
            return None
        if key == "process":
            if raw not in {"first", "subsequent"}:
                return None
            result[key] = raw
        elif key in TIMINGS and re.fullmatch(r"0|[1-9][0-9]{0,7}", raw) and int(raw) <= 20_000_000:
            result[key] = int(raw)
        else:
            return None
    return result if set(result) == TIMINGS | {"process"} else None


def request(config: Configuration, route: str) -> dict[str, Any]:
    if route not in ROUTES:
        raise Refusal("route_refused")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(
        config.origin + "/v1/" + route,
        headers={
            "Authorization": "Bearer " + config.bearer,
            "x-vercel-protection-bypass": config.bypass,
            "Accept": "application/json",
        },
    )
    started = time.perf_counter_ns()
    report: dict[str, Any] = {
        "status": "failed",
        "route": route,
        "http_status": None,
        "edge_cache": "unknown",
        "origin_timing": None,
        "platform_start_type": "unobserved",
        "database_resume": "unobserved",
    }
    try:
        with opener.open(req, timeout=20) as response:
            report["http_status"] = response.status
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise Refusal("response_size_refused")
            body = json.loads(raw)
            cache = response.headers.get("x-vercel-cache", "").upper()
            report["edge_cache"] = cache if cache in CACHE else "unknown"
            if response.headers.get("x-study-policy-sha256") != config.policy_sha256:
                raise Refusal("study_identity_mismatch")
            # Vercel consumes its CDN-specific header; it is not returned to
            # clients. Require the client policy, validate optional forwarded
            # CDN policy, and refuse cache reuse of these authenticated reads.
            if (
                "no-store" not in response.headers.get("cache-control", "")
                or (
                    response.headers.get("cdn-cache-control") is not None
                    and "no-store" not in response.headers["cdn-cache-control"]
                )
                or report["edge_cache"] in {"HIT", "STALE", "PRERENDER"}
            ):
                raise Refusal("private_cache_boundary_changed")
            if not isinstance(body, dict):
                raise Refusal("response_contract_refused")
            if route == "status" and body.get("available") is not True:
                raise Refusal("response_contract_refused")
            if route in {"forecasts", "ticker-filter"} and (
                not isinstance(body.get("items"), list) or len(body["items"]) > 25
            ):
                raise Refusal("response_contract_refused")
            if route == "performance" and (
                type(body.get("forecast_count")) is not int
                or type(body.get("matured_count")) is not int
            ):
                raise Refusal("response_contract_refused")
            report.update(
                status="passed",
                body_bytes=len(raw),
                origin_timing=timing_header(
                    response.headers.get("x-study-read-timing"), report["edge_cache"]
                ),
            )
    except urllib.error.HTTPError as error:
        report.update(reason="http_error", http_status=error.code)
        error.close()
    except Refusal as error:
        report["reason"] = str(error)
    except Exception:
        report["reason"] = "transport_or_json_error"
    report["http_elapsed_us"] = (time.perf_counter_ns() - started) // 1000
    return report


def observe(config: Configuration, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if not config.starts_at <= now < config.expires_at:
        raise Refusal("outside_absolute_study_window")
    slot = int((now - config.starts_at).total_seconds() // 3600)
    route = ROUTES[slot % len(ROUTES)]
    first = request(config, route)
    first["phase"] = "first"
    samples = [first]
    if first["status"] == "passed":
        time.sleep(30)
        if datetime.now(UTC) < config.expires_at:
            paired = request(config, route)
            paired["phase"] = "paired"
            samples.append(paired)
    return {
        "schema_version": 1,
        "scope": "separate_portfolio_study290_observation",
        "observed_at": now.isoformat(),
        "hourly_slot": slot,
        "host_sha256": config.host_sha256,
        "policy_sha256": config.policy_sha256,
        "status": "passed"
        if len(samples) == 2 and all(sample["status"] == "passed" for sample in samples)
        else "degraded",
        "samples": samples,
        "maximum_http_requests": 2,
        "automatic_retries": 0,
        "database_credentials_in_probe": False,
        "response_bodies_retained": False,
        "seven_day_evidence_proven": False,
        "slo_established": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
            raise Refusal("rerun_refused")
        report = observe(Configuration.load(dict(os.environ)))
    except Exception as error:
        report = {
            "schema_version": 1,
            "scope": "separate_portfolio_study290_refusal",
            "status": "refused",
            "observed_at": datetime.now(UTC).isoformat(),
            "reason": str(error)
            if isinstance(error, Refusal)
            else "configuration_or_observation_failed",
            "http_requests": 0 if isinstance(error, Refusal) else None,
            "response_bodies_retained": False,
            "seven_day_evidence_proven": False,
        }
    try:
        os.umask(0o077)
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        print(json.dumps({"status": report["status"], "samples": len(report.get("samples", []))}))
        return 0 if report["status"] == "passed" else 1
    except Exception as error:
        print(
            json.dumps(
                {"status": "refused", "error_type": type(error).__name__, "values_withheld": True}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
