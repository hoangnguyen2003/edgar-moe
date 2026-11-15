from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "private_pilot_probe",
    Path(__file__).resolve().parents[2] / "scripts/probe_private_read_pilot.py",
)
assert _SPEC is not None and _SPEC.loader is not None
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)
ORIGIN = "https://separate-private-pilot.vercel.app"
TOKEN = "a" * 43


def transport(*, fail_case: str = "", failure: str = "") -> httpx.MockTransport:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        name, method, path, authenticated, expected = probe.CASES[calls]
        calls += 1
        assert request.method == method
        assert str(request.url) == ORIGIN + path
        assert request.headers.get("authorization") == (
            "Bearer " + TOKEN if authenticated else None
        )
        if failure == "driver" and name == fail_case:
            raise httpx.ConnectError("private-password host=private-host", request=request)
        payload = {
            "data_mode": "synthetic_fixture",
            "research_only": True,
            "read_policy": {
                "transaction_read_only": True,
                "statement_timeout_ms": 3000,
                "connection_max_per_process": 1,
                "persistent_connections": 0,
                "global_connection_cap_verified": False,
                "server_max_connections": 901,
            },
            "counts": {"forward_forecasts": 1},
            "failed_runs": 1,
            "total": 0 if name == "empty_filter" else 1,
            "items": [] if name == "empty_filter" else [{"private_row": "never-retain"}],
            "forecast_count": 1,
            "matured_count": 0,
        }
        headers = {
            "Cache-Control": "private, no-store",
            "CDN-Cache-Control": "private, no-store",
            "X-Pilot-Read-Timing": "read_ms=12.345;process=subsequent",
        }
        status = expected
        if name == fail_case:
            if failure == "http":
                status = 302
                headers["Location"] = "https://unsafe.example/"
            elif failure == "public_cache":
                headers["Cache-Control"] = "public, max-age=300"
            elif failure == "cors":
                headers["access-control-allow-origin"] = "*"
            elif failure == "policy":
                payload["read_policy"]["transaction_read_only"] = False
            elif failure == "timing":
                headers["X-Pilot-Read-Timing"] = "token=never-retain"
            elif failure == "counts":
                payload["counts"]["forward_forecasts"] = 0
            elif failure == "filter":
                payload["total"] = 3
            elif failure == "performance":
                payload["matured_count"] = 5
            elif failure == "large":
                return httpx.Response(status, content="x" * 20000, headers=headers)
        return httpx.Response(status, json=payload, headers=headers)

    return httpx.MockTransport(handler)


def test_bounded_probe_retains_only_status_policy_timing_not_rows() -> None:
    with httpx.Client(transport=transport(), follow_redirects=False) as client:
        report = probe.observe(ORIGIN, TOKEN, client)
    assert report["status"] == "passed"
    assert len(report["observations"]) == 15
    encoded = json.dumps(report)
    assert TOKEN not in encoded and ORIGIN not in encoded and "never-retain" not in encoded
    assert "no_database_mutation_probes" in report["limitations"]


@pytest.mark.parametrize(
    "case,failure,reason",
    [
        ("anonymous_status", "http", "unexpected_http_status"),
        ("anonymous_status", "public_cache", "private_cache_boundary_failed"),
        ("anonymous_status", "cors", "private_cache_boundary_failed"),
        ("anonymous_status", "driver", "request_failed"),
        ("anonymous_status", "large", "response_too_large"),
        ("status", "policy", "read_policy_failed"),
        ("status", "timing", "timing_missing"),
        ("status", "counts", "synthetic_count_mismatch"),
        ("forecasts", "filter", "synthetic_filter_mismatch"),
        ("performance", "performance", "synthetic_performance_mismatch"),
    ],
)
def test_boundary_failure_stops_and_redacts(case: str, failure: str, reason: str) -> None:
    with httpx.Client(
        transport=transport(fail_case=case, failure=failure), follow_redirects=False
    ) as client:
        report = probe.observe(ORIGIN, TOKEN, client)
    assert report["status"] == "failed" and report["reason"] == reason
    assert len(report["observations"]) < 15
    assert "never-retain" not in json.dumps(report)
    assert "private-password" not in json.dumps(report)


@pytest.mark.parametrize(
    "value",
    [
        "http://x.vercel.app",
        "https://user:pass@x.vercel.app",
        "https://x.vercel.app:443",
        "https://x.vercel.app/token",
        "https://x.vercel.app?token=secret",
        "https://x.vercel.app#secret",
        "https://attacker.example",
        "https://x.vercel.app.attacker.example",
    ],
)
def test_origin_rejected_before_requests(value: str) -> None:
    with pytest.raises(probe.ProbeRefusal, match="invalid_private_pilot_origin"):
        probe.validate_url(value)


def test_token_required_before_network() -> None:
    with (
        httpx.Client(transport=transport()) as client,
        pytest.raises(probe.ProbeRefusal, match="invalid_token"),
    ):
        probe.observe(ORIGIN, "", client)
