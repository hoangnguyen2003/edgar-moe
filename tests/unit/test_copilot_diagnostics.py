from __future__ import annotations

import json
from pathlib import Path

import pytest

from edgar_moe.api.repository import SnapshotRepository
from edgar_moe.copilot.diagnostics import DIAGNOSTIC_DISCLAIMER, DiagnosticSummaryError
from edgar_moe.copilot.tools import ReadOnlyToolset


def _diagnostic() -> dict[str, object]:
    common: dict[str, object] = {
        "diagnostic": True,
        "official_horizon_sessions": 20,
        "horizon_sessions": 5,
        "as_of": "2026-09-19T12:07:18.336364+00:00",
        "status": "ready",
        "forecast_count": 48,
        "matched_count": 48,
        "matured_count": 47,
        "pending_count": 1,
        "unmatched_count": 0,
        "coverage": 47 / 48,
        "rank_ic": -0.2,
        "rmse": 0.06,
        "mae": 0.04,
        "directional_accuracy": 0.4,
        "next_maturity_at": "2026-09-25T20:00:00+00:00",
        "latest_maturity_at": "2026-09-25T20:00:00+00:00",
        "disclaimer": DIAGNOSTIC_DISCLAIMER,
        "observations": [{"event_id": "private-event", "realized_abnormal_return": 0.1}],
        "unmatched_forecasts": [{"forecast_id": "private-forecast"}],
    }
    common["unique_event_evaluation"] = {
        "diagnostic": True,
        "official_horizon_sessions": 20,
        "horizon_sessions": 5,
        "as_of": common["as_of"],
        "status": "ready",
        "forecast_count": 20,
        "matched_count": 20,
        "matured_count": 20,
        "pending_count": 0,
        "unmatched_count": 0,
        "coverage": 1.0,
        "rank_ic": 0.1,
        "rmse": 0.05,
        "mae": 0.03,
        "directional_accuracy": 0.5,
        "next_maturity_at": None,
        "latest_maturity_at": None,
        "disclaimer": DIAGNOSTIC_DISCLAIMER,
        "event_count": 20,
        "repeated_forecast_count": 0,
        "selected_forecast_ids": ["private-forecast"],
        "observations": [{"event_id": "private-event"}],
    }
    return common


def test_summary_is_strictly_redacted_and_content_addressed(tmp_path: Path) -> None:
    path = tmp_path / "diagnostic.json"
    path.write_text(json.dumps(_diagnostic()), encoding="utf-8")

    toolset = ReadOnlyToolset(SnapshotRepository(Path("data/demo/snapshot.json")), diagnostic_path=path)
    result = toolset.execute("get_forward_diagnostic", {})

    assert result.citations[0].source == "snapshot:forward-diagnostic"
    assert result.payload["source_sha256"]
    assert result.payload["forecast_count"] == 48
    assert "observations" not in result.payload
    assert "unmatched_forecasts" not in result.payload
    assert "selected_forecast_ids" not in result.payload["unique_event_evaluation"]
    assert "private-event" not in json.dumps(result.payload)


def test_diagnostic_tool_is_not_advertised_without_explicit_path() -> None:
    toolset = ReadOnlyToolset(SnapshotRepository(Path("data/demo/snapshot.json")))

    assert "get_forward_diagnostic" not in {item.name for item in toolset.definitions()}


def test_invalid_diagnostic_is_reported_without_raw_error_or_path(tmp_path: Path) -> None:
    path = tmp_path / "diagnostic.json"
    path.write_text(json.dumps({"diagnostic": True}), encoding="utf-8")
    toolset = ReadOnlyToolset(SnapshotRepository(Path("data/demo/snapshot.json")), diagnostic_path=path)

    result = toolset.execute("get_forward_diagnostic", {})

    assert result.payload == {
        "available": False,
        "reason": "diagnostic_report_unavailable_or_invalid",
    }
    assert result.citations[0].fields == ("available", "reason")


def test_summary_reader_rejects_non_diagnostic_reports(tmp_path: Path) -> None:
    from edgar_moe.copilot.diagnostics import read_forward_diagnostic_summary

    path = tmp_path / "diagnostic.json"
    payload = _diagnostic()
    payload["diagnostic"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DiagnosticSummaryError, match="marked diagnostic-only"):
        read_forward_diagnostic_summary(path)
