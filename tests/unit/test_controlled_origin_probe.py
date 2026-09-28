from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import benchmark_controlled_origin as controlled  # noqa: E402


def _fake_probe(calls: list[tuple[str, str]], *, cached_route: str | None = None):
    def probe(_opener: object, url: str, *, timeout_seconds: float, route: str) -> dict:
        assert timeout_seconds == 2
        calls.append((route, url))
        if route == "health":
            return {
                "result": "ok",
                "latency_ms": 5.0,
                "edge_cache": "MISS",
                "served_commit_sha": "a" * 40,
            }
        cached = route == cached_route and len(calls) == 4
        sample = {
            "result": "ok",
            "latency_ms": 20.0,
            "edge_cache": "HIT" if cached else "MISS",
            "origin_timing_status": "edge_or_unknown_ignored" if cached else "observed",
        }
        if not cached:
            sample.update(
                {"worker_state": "subsequent", "app_header_ms": 8.0, "registry_read_ms": 6.0}
            )
        return sample

    return probe


def test_controlled_probe_uses_unique_queries_without_retaining_nonces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    nonces = iter(f"private_nonce_{number}" for number in range(15))
    monkeypatch.setattr(controlled, "_probe", _fake_probe(calls))
    monkeypatch.setattr(controlled, "token_hex", lambda _length: next(nonces))
    monkeypatch.setattr(controlled.time, "sleep", lambda _seconds: None)

    report = controlled.measure(
        "https://example.test", expected_commit_sha="a" * 40, timeout_seconds=2
    )

    assert report["status"] == "observed"
    assert report["traffic"]["concurrency"] == 1
    assert report["traffic"]["nonce_retained"] is False
    assert len(calls) == 17
    assert calls[0] == ("health", "https://example.test/api/v1/health")
    assert calls[-1] == ("health", "https://example.test/api/v1/health")
    assert len({url for _, url in calls[1:-1]}) == 15
    assert all("observer_probe=private_nonce_" in url for _, url in calls[1:-1])
    assert "private_nonce_" not in json.dumps(report)
    assert set(report["results"]) == set(controlled.CONTROLLED_ROUTES)
    assert report["results"]["forward_performance"]["fresh_origin_requests"] == 5
    assert report["results"]["forward_performance"]["observed_registry_read_ms"]["p50"] == 6.0


def test_controlled_probe_fails_closed_if_cache_key_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        controlled, "_probe", _fake_probe(calls, cached_route="forward_performance")
    )
    monkeypatch.setattr(controlled.time, "sleep", lambda _seconds: None)

    report = controlled.measure(
        "https://example.test", expected_commit_sha="a" * 40, timeout_seconds=2
    )

    assert report["status"] == "degraded"
    assert report["results"]["forward_performance"]["fresh_origin_requests"] == 4
    assert report["results"]["forward_performance"]["edge_cache_counts"] == {
        "HIT": 1,
        "MISS": 4,
    }


def test_controlled_probe_fails_closed_if_serving_commit_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    probe = _fake_probe(calls)

    def changed_probe(opener: object, url: str, *, timeout_seconds: float, route: str) -> dict:
        result = probe(opener, url, timeout_seconds=timeout_seconds, route=route)
        if route == "health" and len(calls) == 17:
            result["served_commit_sha"] = "b" * 40
        return result

    monkeypatch.setattr(controlled, "_probe", changed_probe)
    monkeypatch.setattr(controlled.time, "sleep", lambda _seconds: None)

    report = controlled.measure(
        "https://example.test", expected_commit_sha="a" * 40, timeout_seconds=2
    )

    assert report["status"] == "degraded"
    assert report["health_identity"]["before_commit_matched"] is True
    assert report["health_identity"]["after_commit_matched"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"expected_commit_sha": "wrong"},
        {"samples_per_route": 4},
        {"samples_per_route": 9},
        {"pause_ms": 499},
        {"timeout_seconds": float("nan")},
    ],
)
def test_controlled_probe_enforces_small_request_budget(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        controlled.measure("https://example.test", **({"expected_commit_sha": "a" * 40} | kwargs))


def test_controlled_report_is_owner_only_and_refuses_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report_path = tmp_path / "controlled.json"
    monkeypatch.setattr(controlled, "measure", lambda *_args, **_kwargs: {"status": "observed"})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_controlled_origin.py",
            "https://example.test",
            "--expect-commit",
            "a" * 40,
            "--output",
            str(report_path),
        ],
    )

    assert controlled.main() == 0
    assert json.loads(report_path.read_text(encoding="utf-8")) == {"status": "observed"}
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        controlled.main()
