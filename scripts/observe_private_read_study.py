"""Two bounded authenticated reads of one fixed synthetic study route.

No database credential, dotenv, provider payload retention or redirect following.
Process-first observations are not relabeled as provider cold starts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

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


@dataclass(frozen=True, repr=False)
class ProviderTrace:
    """Separate, short-lived project credential; never a database credential."""

    token: str = field(repr=False)
    user_id: str = field(repr=False)
    team_id: str = field(repr=False)
    project_id: str = field(repr=False)
    deployment_id: str = field(repr=False)

    @classmethod
    def load(cls, env: dict[str, str]) -> ProviderTrace:
        try:
            values = [
                env["STUDY_TRACE_" + key]
                for key in ("TOKEN", "USER_ID", "TEAM_ID", "PROJECT_ID", "DEPLOYMENT_ID")
            ]
            if any(not re.fullmatch(r"[A-Za-z0-9_.-]{10,512}", v) for v in values):
                raise ValueError()
            if (
                not values[2].startswith("team_")
                or not values[3].startswith("prj_")
                or not values[4].startswith("dpl_")
            ):
                raise ValueError()
            return cls(*values)
        except Exception:
            raise Refusal("trace_configuration_refused") from None

    def api(self, url: str, body: dict[str, str] | None = None) -> dict[str, Any]:
        # Only two provider metadata destinations; redirects and ambient proxies
        # cannot move this project-scoped credential to another host.
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "api.vercel.com":
            raise Refusal("trace_destination_refused")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        req = urllib.request.Request(
            url,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "x-vercel-observability-query-reason": "cli/traces-get",
            },
        )
        try:
            with opener.open(req, timeout=10) as response:
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576:
                    raise Refusal("trace_size_refused")
                document = json.loads(raw)
                if not isinstance(document, dict):
                    raise Refusal("trace_contract_refused")
                return document
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise Refusal("trace_not_ready" if status == 404 else "trace_api_refused") from None
        except Refusal:
            raise
        except Exception:
            raise Refusal("trace_transport_refused") from None

    def session_cookie(self, origin: str) -> str:
        result = self.api(
            "https://api.vercel.com/v1/projects/traces/session?"
            + urlencode({"teamId": self.team_id}),
            {"projectId": self.project_id, "hostname": urlsplit(origin).hostname or ""},
        )
        token = result.get("token")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{20,4096}", token):
            raise Refusal("trace_session_refused")
        return "_vercel_tracing=" + token + "; _vercel_session=" + self.user_id

    def capture(self, request_id: str, route: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,160}", request_id):
            raise Refusal("trace_request_identity_refused")
        url = "https://api.vercel.com/v1/projects/traces?" + urlencode(
            {"teamId": self.team_id, "projectId": self.project_id, "requestId": request_id}
        )
        # Provider ingestion is asynchronous. Retry only its metadata GET after
        # 404, never replay the HTTP application read or a failed credential.
        for attempt in range(3):
            try:
                return summarize_trace(
                    normalize_project_trace(self.api(url)), self, request_id, route
                )
            except Refusal as error:
                if str(error) != "trace_not_ready" or attempt == 2:
                    raise
                time.sleep(10)
        raise Refusal("trace_not_ready")


def otel_nanoseconds(value: Any) -> int:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(part) is not int for part in value)
        or not 0 <= value[0] <= 4_102_444_800
        or not 0 <= value[1] < 1_000_000_000
    ):
        raise Refusal("trace_time_tuple_refused")
    return int(value[0]) * 1_000_000_000 + int(value[1])


def normalize_project_trace(document: dict[str, Any]) -> dict[str, Any]:
    """Normalize the documented project REST API, never a dashboard API fallback.

    REST returns OTEL resources and time tuples, not the dashboard's flattened
    rows or partial flag. Require recorded session resources, finite bounded
    spans, consistent start/end/duration, and a closed tree checked below.
    The receipt states this structural basis; no provider partial flag is invented.
    """
    trace = document.get("trace")
    if trace is None or (isinstance(trace, dict) and trace.get("spans") == []):
        raise Refusal("trace_not_ready")
    if not isinstance(trace, dict):
        raise Refusal("trace_contract_refused")
    for item in (document, trace, document.get("meta", {}), trace.get("meta", {})):
        if isinstance(item, dict) and (
            item.get("partial") is True or item.get("truncated") is True
        ):
            raise Refusal("trace_incomplete")
    resources = trace.get("resources")
    spans = trace.get("spans")
    if (
        not re.fullmatch(r"[a-f0-9]{32}", trace.get("traceId", ""))
        or not isinstance(resources, list)
        or not 1 <= len(resources) <= 8
        or not isinstance(spans, list)
        or not 1 <= len(spans) <= 128
    ):
        raise Refusal("trace_contract_refused")
    by_resource: dict[str, dict[str, Any]] = {}
    for resource in resources:
        if not isinstance(resource, dict):
            raise Refusal("trace_resource_refused")
        name, attrs = resource.get("name"), resource.get("attributes")
        if (
            not isinstance(name, str)
            or name in by_resource
            or not isinstance(attrs, dict)
            or attrs.get("service.name") != name
            or attrs.get("vercel.is_session_trace") not in (True, "true")
            or attrs.get("vercel.should_store_trace") not in (True, "true")
        ):
            raise Refusal("trace_recorded_session_refused")
        by_resource[name] = attrs
    normalized = []
    for span in spans:
        if not isinstance(span, dict):
            raise Refusal("trace_contract_refused")
        resource = span.get("resource")
        if not isinstance(resource, str) or resource not in by_resource:
            raise Refusal("trace_resource_refused")
        if not isinstance(span.get("library"), dict) or span["library"].get("name") != resource:
            raise Refusal("trace_provider_library_refused")
        start = otel_nanoseconds(span.get("startTime"))
        end = otel_nanoseconds(span.get("endTime"))
        duration = otel_nanoseconds(span.get("duration"))
        if end < start or abs(end - start - duration) > 1_000_000:
            raise Refusal("trace_duration_inconsistent")
        attrs = span.get("attributes")
        if not isinstance(attrs, dict):
            raise Refusal("trace_contract_refused")
        normalized.append(
            {
                "spanId": span.get("spanId"),
                "parentSpanId": span.get("parentSpanId"),
                "isRoot": span.get("spanId") == trace.get("rootSpanId"),
                "name": span.get("name"),
                "requestId": attrs.get("vercel.request_id"),
                "deploymentId": by_resource[resource].get("vercel.deployment_id"),
                "resourceAttributes": by_resource[resource],
                "serviceName": resource,
                "durationMs": duration / 1_000_000,
                "timestamp": datetime.fromtimestamp(start // 1_000_000_000, UTC)
                .replace(microsecond=(start % 1_000_000_000) // 1000)
                .isoformat(),
            }
        )
    return {
        "rootSpanId": trace.get("rootSpanId"),
        "spans": normalized,
        "meta": {
            "partial": False,
            "spanCount": len(normalized),
            "completionBasis": "recorded_session_closed_tree",
            "providerPartialFlag": None,
        },
    }


def summarize_trace(
    document: dict[str, Any], target: ProviderTrace, request_id: str, route: str
) -> dict[str, Any]:
    """Allowlist provider evidence; no raw attributes, headers, URLs or IDs escape."""
    spans = document.get("spans")
    meta = document.get("meta")
    if (
        not isinstance(meta, dict)
        or meta.get("partial") is not False
        or not isinstance(spans, list)
        or not 1 <= len(spans) <= 128
        or meta.get("spanCount") != len(spans)
    ):
        raise Refusal("trace_incomplete")
    by_id: dict[str, dict[str, Any]] = {}
    for span in spans:
        if not isinstance(span, dict):
            raise Refusal("trace_contract_refused")
        identity = span.get("spanId")
        resource = span.get("resourceAttributes")
        duration = span.get("durationMs")
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"[a-f0-9]{16}", identity)
            or identity in by_id
            or not isinstance(resource, dict)
            or resource.get("vercel.project_id") != target.project_id
            or resource.get("vercel.owner_id") != target.team_id
            or resource.get("vercel.deployment_id") != target.deployment_id
            or span.get("deploymentId") != target.deployment_id
            or not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(duration)
            or not 0 <= duration <= 120_000
        ):
            raise Refusal("trace_identity_or_duration_refused")
        by_id[identity] = span
    roots = [span for span in spans if span.get("isRoot") is True]
    if (
        len(roots) != 1
        or document.get("rootSpanId") != roots[0]["spanId"]
        or roots[0].get("requestId") != request_id
        or roots[0].get("name") != "GET /v1/" + route
        or roots[0].get("serviceName") != "vercel.edge-network"
    ):
        raise Refusal("trace_request_identity_refused")
    root_id = roots[0]["spanId"]
    for identity in by_id:
        seen = set()
        while identity != root_id:
            if identity in seen or identity not in by_id:
                raise Refusal("trace_ancestry_refused")
            seen.add(identity)
            identity = by_id[identity].get("parentSpanId", "")
    runtime = [
        s
        for s in spans
        if s.get("serviceName") == "vercel.serverless-runtime"
        and s["resourceAttributes"].get("service.name") == "vercel.serverless-runtime"
    ]
    invocations = [s for s in runtime if s.get("name") == "Invoke Function"]
    if len(invocations) != 1:
        raise Refusal("trace_invocation_ambiguous")
    invocation = invocations[0]
    startup_names = {"Start VM", "Spawn Python", "Init User Code"}
    if any(s.get("name") not in startup_names | {"Invoke Function", "waitUntil"} for s in runtime):
        raise Refusal("trace_runtime_schema_changed")
    startup = [s for s in runtime if s.get("name") in startup_names]
    if any(s.get("parentSpanId") != invocation["spanId"] for s in startup):
        raise Refusal("trace_startup_ancestry_refused")
    try:
        root_start = datetime.fromisoformat(roots[0]["timestamp"].replace("Z", "+00:00"))
        root_end = root_start + timedelta(milliseconds=roots[0]["durationMs"])
        if root_start.utcoffset() != timedelta(0):
            raise ValueError()
        for item in [invocation, *startup]:
            began = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
            ended = began + timedelta(milliseconds=item["durationMs"])
            # Millisecond timestamp rounding permits five milliseconds only.
            # Historical prewarming must not become request-time cold evidence.
            if (
                began.utcoffset() != timedelta(0)
                or began < root_start - timedelta(milliseconds=5)
                or ended > root_end + timedelta(milliseconds=5)
            ):
                raise ValueError()
    except Exception:
        raise Refusal("trace_startup_outside_request") from None
    names = {s["name"] for s in startup}
    if len(names) != len(startup):
        raise Refusal("trace_startup_ambiguous")
    # Report provider-observed boot/init separately; do not invent the paid
    # functionStartType enum or substitute our process-first application marker.
    classification = "cold" if names else "warm"
    return {
        "status": "passed",
        "source": "vercel_project_trace_rest",
        "classification": classification,
        "basis": "provider_startup_spans"
        if names
        else "complete_provider_invocation_without_startup",
        "vm_start_observed": "Start VM" in names,
        "python_spawn_observed": "Spawn Python" in names,
        "user_code_init_observed": "Init User Code" in names,
        "startup_ms": {s["name"]: s["durationMs"] for s in startup},
        "invocation_ms": invocation["durationMs"],
        "request_sha256": hashlib.sha256(request_id.encode()).hexdigest(),
        "provider_span_count": len(spans),
        "trace_tree_validated": True,
        "completion_basis": meta.get("completionBasis", "provider_partial_flag"),
        "provider_partial_flag": meta.get("providerPartialFlag", meta.get("partial")),
        "raw_provider_payload_retained": False,
    }


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


def request(
    config: Configuration, route: str, *, trace_cookie: str | None = None
) -> dict[str, Any]:
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
    if trace_cookie is not None:
        req.add_header("Cookie", trace_cookie)
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
            if trace_cookie is not None:
                report["_provider_request_id"] = response.headers.get("x-vercel-id", "").rsplit(
                    "::", 1
                )[-1]
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
            timings = timing_header(
                response.headers.get("x-study-read-timing"), report["edge_cache"]
            )
            if timings is None:
                raise Refusal("origin_timing_refused")
            report.update(status="passed", body_bytes=len(raw), origin_timing=timings)
    except urllib.error.HTTPError as error:
        report.update(reason="http_error", http_status=error.code)
        if trace_cookie is not None:
            report["_provider_request_id"] = error.headers.get("x-vercel-id", "").rsplit("::", 1)[
                -1
            ]
        error.close()
    except Refusal as error:
        report["reason"] = str(error)
    except Exception:
        report["reason"] = "transport_or_json_error"
    report["http_elapsed_us"] = (time.perf_counter_ns() - started) // 1000
    return report


def observe(
    config: Configuration, *, now: datetime | None = None, trace: ProviderTrace | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if not config.starts_at <= now < config.expires_at:
        raise Refusal("outside_absolute_study_window")
    slot = int((now - config.starts_at).total_seconds() // 3600)
    route = ROUTES[slot % len(ROUTES)]
    cookie = trace.session_cookie(config.origin) if trace else None
    first = request(config, route, trace_cookie=cookie) if trace else request(config, route)
    first["phase"] = "first"
    samples = [first]
    if first["status"] == "passed":
        time.sleep(30)
        if datetime.now(UTC) < config.expires_at:
            paired = (
                request(config, route, trace_cookie=cookie) if trace else request(config, route)
            )
            paired["phase"] = "paired"
            samples.append(paired)
    if trace:
        for sample in samples:
            request_id = sample.pop("_provider_request_id", "")
            try:
                evidence = trace.capture(request_id, route)
                sample["provider_trace"] = evidence
                sample["platform_start_type"] = evidence["classification"]
            except Refusal as error:
                sample["provider_trace"] = {"status": "refused", "reason": str(error)}
    return {
        "schema_version": 2,
        "scope": "separate_portfolio_study290_observation",
        "observed_at": now.isoformat(),
        "hourly_slot": slot,
        "host_sha256": config.host_sha256,
        "policy_sha256": config.policy_sha256,
        "status": "passed"
        if len(samples) == 2
        and all(
            sample["status"] == "passed"
            and (trace is None or sample.get("provider_trace", {}).get("status") == "passed")
            for sample in samples
        )
        else "degraded",
        "samples": samples,
        "maximum_http_requests": 2,
        "automatic_retries": 0,
        "provider_trace_enabled": trace is not None,
        "maximum_trace_metadata_get_attempts_per_sample": 3 if trace else 0,
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
        env = dict(os.environ)
        trace = ProviderTrace.load(env) if env.get("STUDY_TRACE_ENABLED") == "true" else None
        report = observe(Configuration.load(env), trace=trace)
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
