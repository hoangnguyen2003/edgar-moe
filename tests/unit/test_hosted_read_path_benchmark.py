from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import benchmark_hosted_read_path as benchmark  # noqa: E402


class _Response:
    status = 200

    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self.body = body
        self.headers = {"Content-Type": content_type, "X-Vercel-Cache": "MISS"}

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


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_Response(b'{"available":false,"health_status":"degraded"}'), "semantic_degraded"),
        (_Response(b'{"available":true,"health_status":"unexpected"}'), "contract_error"),
        (_Response(b"not JSON"), "contract_error"),
        (_Response(b"{}", "text/html"), "contract_error"),
        (_Response(b"x" * (benchmark._MAX_BODY_BYTES + 1)), "contract_error"),
        (HTTPError("https://example.test", 503, "unavailable", {}, None), "http_error"),
        (URLError("offline"), "transport_error"),
    ],
)
def test_probe_classifies_degradation_and_failure_without_body(
    result: _Response | Exception, expected: str
) -> None:
    observed = benchmark._probe(
        _Opener(result),
        "https://example.test/api/v1/forward/status",
        timeout_seconds=2,
        route="forward_status",
    )
    assert observed["result"] == expected
    assert "body" not in observed


def test_bounded_round_robin_report_counts_failures_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_probe(_opener: object, _url: str, *, timeout_seconds: float, route: str) -> dict:
        assert timeout_seconds == 2
        calls.append(route)
        if len(calls) > len(benchmark.ROUTES) and route == "forecasts_page" and len(calls) == 8:
            return {"result": "http_error", "http_status": 503, "latency_ms": 4.0}
        return {
            "result": "ok",
            "http_status": 200,
            "latency_ms": 10.0,
            "edge_cache": "MISS",
            "semantic_status": "ok",
            "registry_available": True,
        }

    monkeypatch.setattr(benchmark, "_probe", fake_probe)
    monkeypatch.setattr(benchmark.time, "sleep", lambda _seconds: None)
    report = benchmark.measure(
        "https://example.test", samples_per_route=5, pause_ms=200, timeout_seconds=2
    )
    assert calls == list(benchmark.ROUTES) * 6
    assert report["status"] == "degraded"
    assert report["traffic"]["concurrency"] == 1
    assert report["results"]["health"]["successful_latency_ms"]["p95"] == 10
    assert report["results"]["forecasts_page"]["result_counts"] == {"http_error": 1, "ok": 4}
    assert report["results"]["forecasts_page"]["http_status_counts"] == {"200": 4, "503": 1}


def test_forward_quality_warning_is_visible_without_calling_it_an_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_probe(_opener: object, _url: str, *, timeout_seconds: float, route: str) -> dict:
        return {
            "result": "ok",
            "http_status": 200,
            "latency_ms": 10.0,
            "edge_cache": "MISS",
            "semantic_status": "warning" if route == "forward_status" else "ok",
            "registry_available": True,
        }

    monkeypatch.setattr(benchmark, "_probe", fake_probe)
    monkeypatch.setattr(benchmark.time, "sleep", lambda _seconds: None)
    report = benchmark.measure("https://example.test", samples_per_route=5, pause_ms=200)
    assert report["status"] == "warning"
    assert report["results"]["forward_status"]["semantic_status_counts"] == {"warning": 5}


def test_benchmark_fails_closed_on_served_commit_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_probe(_opener: object, _url: str, *, timeout_seconds: float, route: str) -> dict:
        return {
            "result": "ok",
            "http_status": 200,
            "latency_ms": 10.0,
            "edge_cache": "MISS",
            "semantic_status": "ok",
            "registry_available": True,
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
