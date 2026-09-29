from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import benchmark_hosted_read_path as benchmark  # noqa: E402

_REVIEW_DETAIL = (
    "Prospective forecasts and outcomes are withheld from the public application "
    "pending source-rights review. The registry remains private."
)


def _withheld_error(*, detail: str = _REVIEW_DETAIL, cache_control: str = "no-store") -> HTTPError:
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": cache_control,
        "X-Vercel-Cache": "BYPASS",
    }
    return HTTPError(
        "https://example.test/api/v1/forward/performance",
        410,
        "Gone",
        headers,
        BytesIO(json.dumps({"detail": detail}).encode()),
    )


def _fake_route_result(route: str, *, latency_ms: float = 10.0) -> dict:
    if route in {"forecasts_page", "forward_performance"}:
        return {
            "result": "expected_withheld",
            "http_status": 410,
            "latency_ms": latency_ms,
            "edge_cache": "BYPASS",
            "public_visibility": "withheld_review",
        }
    if route == "forward_status":
        return {
            "result": "ok",
            "http_status": 200,
            "latency_ms": latency_ms,
            "edge_cache": "MISS",
            "semantic_status": "withheld_review",
            "public_visibility": "withheld_review",
            "health_reason": "publication_withheld_review",
        }
    return {
        "result": "ok",
        "http_status": 200,
        "latency_ms": latency_ms,
        "edge_cache": "MISS",
    }


class _Response:
    status = 200

    def __init__(
        self,
        body: bytes,
        content_type: str = "application/json",
        *,
        edge_cache: str = "MISS",
        origin_timing: str | None = None,
    ) -> None:
        self.body = body
        self.headers = {"Content-Type": content_type, "X-Vercel-Cache": edge_cache}
        if origin_timing is not None:
            self.headers["X-EDGAR-Read-Timing"] = origin_timing

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


class _Opener:
    def __init__(self, result: _Response | Exception) -> None:
        self.result = result
        self.request: Request | None = None

    def open(self, request: Request, *, timeout: float) -> _Response:
        self.request = request
        assert timeout == 2
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.test",
        "https://user:secret@example.test",
        "https://example.test/path",
        "https://example.test?token=secret",
        "https://example.test/#fragment",
    ],
)
def test_benchmark_rejects_unsafe_origins(origin: str) -> None:
    with pytest.raises(ValueError, match="credential-free HTTPS origin"):
        benchmark.normalize_origin(origin)


def test_benchmark_accepts_https_and_http_loopback_only() -> None:
    assert benchmark.normalize_origin("https://example.test/") == "https://example.test"
    assert benchmark.normalize_origin("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert (
        benchmark._RejectRedirect().redirect_request(
            Request("https://example.test"), None, 302, "redirect", {}, "https://other.test"
        )
        is None
    )


def test_probe_never_sends_credentials_or_retains_body() -> None:
    opener = _Opener(
        _Response(
            b'{"status":"ok","snapshot_loaded":true,"commit_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","secret":"DO_NOT_KEEP"}'
        )
    )
    result = benchmark._probe(
        opener, "https://example.test/api/v1/health", timeout_seconds=2, route="health"
    )
    assert result["result"] == "ok"
    assert result["edge_cache"] == "MISS"
    assert result["served_commit_sha"] == "a" * 40
    assert "DO_NOT_KEEP" not in json.dumps(result)
    assert opener.request is not None
    assert opener.request.get_method() == "GET"
    assert "Authorization" not in opener.request.headers


def test_probe_never_retains_untrusted_semantic_strings() -> None:
    secret = "PRIVATE_TEXT_MUST_NOT_APPEAR"
    observed = benchmark._probe(
        _Opener(_Response(json.dumps({"status": secret, "snapshot_loaded": True}).encode())),
        "https://example.test/api/v1/health",
        timeout_seconds=2,
        route="health",
    )
    assert observed["result"] != "ok"
    assert secret not in json.dumps(observed)
    observed = benchmark._probe(
        _Opener(_Response(json.dumps({"health_status": secret, "available": [secret]}).encode())),
        "https://example.test/api/v1/forward/status",
        timeout_seconds=2,
        route="forward_status",
    )
    assert observed["result"] == "contract_error"
    assert secret not in json.dumps(observed)


def test_forward_status_reports_only_the_fixed_withheld_policy() -> None:
    observed = benchmark._probe(
        _Opener(
            _Response(
                json.dumps(
                    {
                        "public_visibility": "withheld_review",
                        "message": _REVIEW_DETAIL,
                    }
                ).encode()
            )
        ),
        "https://example.test/api/v1/forward/status",
        timeout_seconds=2,
        route="forward_status",
    )
    assert observed["result"] == "ok"
    assert observed["public_visibility"] == "withheld_review"
    assert observed["health_reason"] == "publication_withheld_review"

    invalid = benchmark._probe(
        _Opener(
            _Response(
                json.dumps(
                    {
                        "public_visibility": "withheld_review",
                        "message": _REVIEW_DETAIL,
                        "private": "DO_NOT_KEEP",
                    }
                ).encode()
            )
        ),
        "https://example.test/api/v1/forward/status",
        timeout_seconds=2,
        route="forward_status",
    )
    assert invalid["result"] == "contract_error"
    assert "DO_NOT_KEEP" not in json.dumps(invalid)

    invalid = benchmark._probe(
        _Opener(
            _Response(
                json.dumps(
                    {
                        "public_visibility": "available",
                        "message": "PRIVATE_DETAILS",
                    }
                ).encode()
            )
        ),
        "https://example.test/api/v1/forward/status",
        timeout_seconds=2,
        route="forward_status",
    )
    assert invalid["result"] == "contract_error"
    assert "PRIVATE_DETAILS" not in json.dumps(invalid)


def test_origin_timing_is_numeric_allowlisted_and_ignored_on_edge_hits() -> None:
    header = "app_header_ms=12.345;worker=first;registry_read_ms=7.890"
    observed = benchmark._probe(
        _Opener(_Response(b"{}", origin_timing=header)),
        "https://example.test/api/v1/summary",
        timeout_seconds=2,
        route="summary",
    )
    assert observed["origin_timing_status"] == "observed"
    assert observed["worker_state"] == "first"
    assert observed["app_header_ms"] == 12.345
    assert observed["registry_read_ms"] == 7.89
    assert header not in json.dumps(observed)

    cached = benchmark._probe(
        _Opener(_Response(b"{}", edge_cache="HIT", origin_timing=header)),
        "https://example.test/api/v1/summary",
        timeout_seconds=2,
        route="summary",
    )
    assert cached["origin_timing_status"] == "edge_or_unknown_ignored"
    assert "worker_state" not in cached and "app_header_ms" not in cached

    malformed = benchmark._probe(
        _Opener(_Response(b"{}", origin_timing=header + ";secret=PRIVATE_VALUE")),
        "https://example.test/api/v1/summary",
        timeout_seconds=2,
        route="summary",
    )
    assert malformed["origin_timing_status"] == "invalid"
    assert "PRIVATE_VALUE" not in json.dumps(malformed)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_withheld_error(), "expected_withheld"),
        (_withheld_error(detail="UNTRUSTED_PRIVATE_TEXT"), "contract_error"),
        (_withheld_error(cache_control="public, max-age=3600"), "contract_error"),
        (_Response(b"{}"), "contract_error"),
        (_Response(b"not JSON"), "contract_error"),
        (_Response(b"{}", "text/html"), "contract_error"),
        (_Response(b"x" * (benchmark._MAX_BODY_BYTES + 1)), "contract_error"),
        (HTTPError("https://example.test", 503, "unavailable", {}, None), "http_error"),
        (URLError("offline"), "transport_error"),
    ],
)
def test_evidence_probe_accepts_only_fixed_noncacheable_410(
    result: _Response | Exception, expected: str
) -> None:
    observed = benchmark._probe(
        _Opener(result),
        "https://example.test/api/v1/forward/performance",
        timeout_seconds=2,
        route="forward_performance",
    )
    assert observed["result"] == expected
    assert "body" not in observed
    assert "UNTRUSTED_PRIVATE_TEXT" not in json.dumps(observed)


def test_performance_probe_rejects_public_aggregate_even_when_well_formed() -> None:
    valid = benchmark._probe(
        _Opener(
            _Response(b'{"forecast_count":3,"matured_count":2,"pending_count":1,"coverage":0.666}')
        ),
        "https://example.test/api/v1/forward/performance",
        timeout_seconds=2,
        route="forward_performance",
    )
    assert valid["result"] == "contract_error"
    assert "forecast_count" not in valid and "body" not in valid

    invalid = benchmark._probe(
        _Opener(
            _Response(b'{"forecast_count":3,"matured_count":2,"pending_count":2,"coverage":0.5}')
        ),
        "https://example.test/api/v1/forward/performance",
        timeout_seconds=2,
        route="forward_performance",
    )
    assert invalid["result"] == "contract_error"


def test_bounded_round_robin_report_counts_failures_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_probe(_opener: object, _url: str, *, timeout_seconds: float, route: str) -> dict:
        assert timeout_seconds == 2
        calls.append(route)
        first_forecast_sample = (
            len(benchmark.ROUTES) + list(benchmark.ROUTES).index("forecasts_page") + 1
        )
        if route == "forecasts_page" and len(calls) == first_forecast_sample:
            return {"result": "http_error", "http_status": 503, "latency_ms": 4.0}
        return _fake_route_result(route)

    monkeypatch.setattr(benchmark, "_probe", fake_probe)
    monkeypatch.setattr(benchmark.time, "sleep", lambda _seconds: None)
    report = benchmark.measure(
        "https://example.test", samples_per_route=5, pause_ms=200, timeout_seconds=2
    )
    assert calls == list(benchmark.ROUTES) * 6
    assert report["status"] == "degraded"
    assert report["traffic"]["concurrency"] == 1
    assert report["results"]["health"]["successful_latency_ms"]["p95"] == 10
    assert report["schema_version"] == 6
    assert report["results"]["forward_performance"]["requests"] == 5
    assert report["results"]["health"]["successful_latency_by_edge_cache_ms"] == {
        "MISS": {"requests": 5, "p50": 10.0, "p95": 10.0, "p99": 10.0, "max": 10.0}
    }
    assert len(report["sample_observations"]["health"]) == 5
    assert report["results"]["forecasts_page"]["result_counts"] == {
        "expected_withheld": 4,
        "http_error": 1,
    }
    assert report["results"]["forecasts_page"]["http_status_counts"] == {"410": 4, "503": 1}


def test_successful_latency_is_split_by_observed_edge_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_probe(_opener: object, _url: str, *, timeout_seconds: float, route: str) -> dict:
        nonlocal calls
        calls += 1
        cache = "HIT" if (calls // len(benchmark.ROUTES)) % 2 else "MISS"
        return {
            **_fake_route_result(route, latency_ms=5.0 if cache == "HIT" else 50.0),
            "edge_cache": cache,
            "served_commit_sha": "a" * 40 if route == "health" else None,
        }

    monkeypatch.setattr(benchmark, "_probe", fake_probe)
    monkeypatch.setattr(benchmark.time, "sleep", lambda _seconds: None)
    report = benchmark.measure("https://example.test", samples_per_route=5, pause_ms=200)
    health = report["results"]["health"]
    assert health["successful_latency_ms"]["p50"] == 5.0
    assert health["successful_latency_by_edge_cache_ms"]["HIT"]["p50"] == 5.0
    assert health["successful_latency_by_edge_cache_ms"]["MISS"]["p50"] == 50.0
    assert len(report["sample_observations"]["health"]) == 5


def test_successful_origin_latency_is_split_by_first_and_subsequent_process_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_probe(_opener: object, _url: str, *, timeout_seconds: float, route: str) -> dict:
        nonlocal calls
        calls += 1
        worker = "first" if calls <= len(benchmark.ROUTES) else "subsequent"
        result = _fake_route_result(route, latency_ms=80.0 if worker == "first" else 10.0)
        if route not in {"forecasts_page", "forward_performance"}:
            result.update(
                {
                    "origin_timing_status": "observed",
                    "worker_state": worker,
                    "app_header_ms": 40.0 if worker == "first" else 5.0,
                }
            )
        return {
            **result,
            "served_commit_sha": "a" * 40 if route == "health" else None,
        }

    monkeypatch.setattr(benchmark, "_probe", fake_probe)
    monkeypatch.setattr(benchmark.time, "sleep", lambda _seconds: None)
    report = benchmark.measure("https://example.test", samples_per_route=5, pause_ms=200)
    # The first request per route is a warmup and is excluded from measured samples.
    assert report["warmup_observations"]["health"]["worker_state"] == "first"
    assert report["results"]["health"]["successful_origin_latency_by_worker_ms"] == {
        "subsequent": {"requests": 5, "p50": 10.0, "p95": 10.0, "p99": 10.0, "max": 10.0}
    }


def test_expected_publication_hold_is_visible_without_calling_it_an_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_probe(_opener: object, _url: str, *, timeout_seconds: float, route: str) -> dict:
        return _fake_route_result(route)

    monkeypatch.setattr(benchmark, "_probe", fake_probe)
    monkeypatch.setattr(benchmark.time, "sleep", lambda _seconds: None)
    report = benchmark.measure("https://example.test", samples_per_route=5, pause_ms=200)
    assert report["status"] == "observed"
    assert report["results"]["forward_status"]["semantic_status_counts"] == {"withheld_review": 5}
    assert report["results"]["forward_status"]["health_reason_counts"] == {
        "publication_withheld_review": 5
    }


def test_benchmark_fails_closed_on_served_commit_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_probe(_opener: object, _url: str, *, timeout_seconds: float, route: str) -> dict:
        return {
            **_fake_route_result(route),
            "served_commit_sha": "b" * 40 if route == "health" else None,
        }

    monkeypatch.setattr(benchmark, "_probe", fake_probe)
    monkeypatch.setattr(benchmark.time, "sleep", lambda _seconds: None)
    report = benchmark.measure(
        "https://example.test",
        samples_per_route=5,
        pause_ms=200,
        expected_commit_sha="a" * 40,
    )
    assert report["status"] == "degraded"
    assert report["warmup_result"]["health"] == "identity_mismatch"
    assert report["results"]["health"]["result_counts"] == {"identity_mismatch": 5}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"samples_per_route": 4},
        {"samples_per_route": 21},
        {"pause_ms": 100},
        {"timeout_seconds": float("nan")},
    ],
)
def test_benchmark_enforces_small_request_budget(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        benchmark.measure("https://example.test", **kwargs)


def test_cli_refuses_existing_output_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "existing.json"
    output.write_text("untouched", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["benchmark", "https://example.test", "--output", str(output)])
    monkeypatch.setattr(
        benchmark, "measure", lambda *_args, **_kwargs: pytest.fail("network was attempted")
    )
    with pytest.raises(ValueError, match="refusing to overwrite"):
        benchmark.main()
    assert output.read_text(encoding="utf-8") == "untouched"
