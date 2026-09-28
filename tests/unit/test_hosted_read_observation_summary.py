from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from scripts import summarize_hosted_read_observations as summary


def _report(path: Path, day: date, *, origin: str = "https://example.test") -> Path:
    sample = {
        "result": "ok",
        "latency_ms": 12.0,
        "edge_cache": "HIT",
        "served_commit_sha": "a" * 40,
        "body": "PRIVATE_TEXT_MUST_NOT_APPEAR",
    }
    report = {
        "schema_version": 2,
        "scope": "hosted_public_api_sequential_read_only_observation",
        "origin": origin,
        "captured_at": f"{day.isoformat()}T09:00:00+00:00",
        "traffic": {"samples_per_route": 5, "expected_commit_sha": "a" * 40},
        "status": "observed",
        "sample_observations": {name: [sample] * 5 for name in summary.ROUTES},
    }
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_summary_requires_seven_consecutive_utc_days_and_ignores_extra_fields(
    tmp_path: Path,
) -> None:
    start = date(2026, 9, 1)
    paths = [_report(tmp_path / f"{n}.json", start + timedelta(days=n)) for n in range(7)]
    result = summary.summarize(paths)
    assert result["seven_day_coverage"] is True
    assert result["longest_consecutive_utc_days"] == 7
    assert result["results"]["health"]["requests"] == 35
    assert result["results"]["health"]["successful_latency_by_edge_cache_ms"]["HIT"]["p95"] == 12.0
    assert "PRIVATE_TEXT_MUST_NOT_APPEAR" not in json.dumps(result)


def test_missing_day_is_not_misreported_as_week(tmp_path: Path) -> None:
    start = date(2026, 9, 1)
    paths = [
        _report(tmp_path / f"{n}.json", start + timedelta(days=n)) for n in (0, 1, 2, 4, 5, 6, 7)
    ]
    result = summary.summarize(paths)
    assert result["distinct_utc_days"] == 7
    assert result["longest_consecutive_utc_days"] == 4
    assert result["seven_day_coverage"] is False


def test_summary_rejects_mixed_origins_and_unpinned_reports(tmp_path: Path) -> None:
    first = _report(tmp_path / "one.json", date(2026, 9, 1))
    second = _report(tmp_path / "two.json", date(2026, 9, 2), origin="https://other.test")
    with pytest.raises(ValueError, match="different origins"):
        summary.summarize([first, second])
    report = json.loads(first.read_text(encoding="utf-8"))
    report["traffic"]["expected_commit_sha"] = None
    first.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="serving commit is required"):
        summary.summarize([first])


def test_summary_rejects_unbounded_or_missing_samples(tmp_path: Path) -> None:
    path = _report(tmp_path / "bad.json", date(2026, 9, 1))
    report = json.loads(path.read_text(encoding="utf-8"))
    report["sample_observations"]["health"] = []
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="missing bounded samples"):
        summary.summarize([path])


def test_summary_rejects_health_identity_mismatch(tmp_path: Path) -> None:
    path = _report(tmp_path / "wrong-commit.json", date(2026, 9, 1))
    report = json.loads(path.read_text(encoding="utf-8"))
    report["sample_observations"]["health"][0]["served_commit_sha"] = "b" * 40
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match expected commit"):
        summary.summarize([path])


def test_summary_accepts_mixed_v2_v3_without_backfilling_missing_origin_evidence(
    tmp_path: Path,
) -> None:
    old = _report(tmp_path / "old.json", date(2026, 9, 1))
    newer = _report(tmp_path / "new.json", date(2026, 9, 2))
    report = json.loads(newer.read_text(encoding="utf-8"))
    report["schema_version"] = 3
    for route, items in report["sample_observations"].items():
        report["sample_observations"][route] = [
            {**item, "origin_timing_status": "edge_or_unknown_ignored"} for item in items
        ]
    report["warmup_observations"] = {
        route: {**items[0], "origin_timing_status": "edge_or_unknown_ignored"}
        for route, items in report["sample_observations"].items()
    }
    report["warmup_observations"]["forward_status"] = {
        "result": "ok",
        "latency_ms": 70.0,
        "edge_cache": "MISS",
        "origin_timing_status": "observed",
        "worker_state": "first",
        "app_header_ms": 20.0,
        "registry_read_ms": 12.0,
    }
    report["sample_observations"]["forward_status"] = [
        {
            "result": "ok",
            "latency_ms": 10.0,
            "edge_cache": "MISS",
            "origin_timing_status": "observed",
            "worker_state": "subsequent",
            "app_header_ms": 5.0,
            "registry_read_ms": 2.0,
            "raw_header": "PRIVATE_NOT_RETAINED",
        }
        for _ in range(5)
    ]
    newer.write_text(json.dumps(report), encoding="utf-8")

    result = summary.summarize([old, newer])

    observed = result["results"]["forward_status"]
    assert observed["origin_timing_status_counts"] == {"legacy_v2": 5, "observed": 5}
    assert observed["successful_origin_latency_by_worker_ms"]["subsequent"]["requests"] == 5
    assert observed["observed_registry_read_ms"]["requests"] == 5
    assert (
        result["warmup_results"]["forward_status"]["successful_client_latency_by_worker_ms"][
            "first"
        ]["requests"]
        == 1
    )
    assert "PRIVATE_NOT_RETAINED" not in json.dumps(result)


def test_summary_rejects_cached_or_malformed_origin_evidence(tmp_path: Path) -> None:
    path = _report(tmp_path / "bad-origin.json", date(2026, 9, 1))
    report = json.loads(path.read_text(encoding="utf-8"))
    report["schema_version"] = 3
    for route, items in report["sample_observations"].items():
        report["sample_observations"][route] = [
            {**item, "origin_timing_status": "edge_or_unknown_ignored"} for item in items
        ]
    report["warmup_observations"] = {
        route: items[0] for route, items in report["sample_observations"].items()
    }
    item = report["sample_observations"]["forward_status"][0]
    item.update(
        {
            "origin_timing_status": "observed",
            "worker_state": "first",
            "app_header_ms": 3.0,
        }
    )
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="cached timing"):
        summary.summarize([path])

    item["edge_cache"] = "MISS"
    item["app_header_ms"] = float("nan")
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid origin timing metric"):
        summary.summarize([path])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", True, "schema v2 or v3"),
        ("status", [], "invalid report status"),
        ("sample_result", [], "invalid sample"),
    ],
)
def test_summary_rejects_malformed_report_types(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    path = _report(tmp_path / "bad-types.json", date(2026, 9, 1))
    report = json.loads(path.read_text(encoding="utf-8"))
    if field == "sample_result":
        report["sample_observations"]["forward_status"][0]["result"] = value
    else:
        report[field] = value
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        summary.summarize([path])
