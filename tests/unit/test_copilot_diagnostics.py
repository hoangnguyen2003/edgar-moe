from __future__ import annotations

import json
from pathlib import Path

import pytest

from edgar_moe.api.repository import SnapshotRepository
from edgar_moe.copilot.diagnostics import DIAGNOSTIC_DISCLAIMER, DiagnosticSummaryError
from edgar_moe.copilot.tools import ReadOnlyToolset
from edgar_moe.forward.diagnostic_history import (
    build_forward_diagnostic_history,
    load_forward_diagnostic_reports,
)


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

    toolset = ReadOnlyToolset(
        SnapshotRepository(Path("data/demo/snapshot.json")), diagnostic_path=path
    )
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

    names = {item.name for item in toolset.definitions()}

    assert "get_forward_diagnostic" not in names
    assert "get_forward_diagnostic_history" not in names


def test_invalid_diagnostic_is_reported_without_raw_error_or_path(tmp_path: Path) -> None:
    path = tmp_path / "diagnostic.json"
    path.write_text(json.dumps({"diagnostic": True}), encoding="utf-8")
    toolset = ReadOnlyToolset(
        SnapshotRepository(Path("data/demo/snapshot.json")), diagnostic_path=path
    )

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


def _write_history(tmp_path: Path) -> Path:
    summaries: list[dict[str, object]] = []
    for index in range(1, 4):
        as_of = f"2026-09-{16 + index:02d}T12:07:18+00:00"
        payload = _diagnostic()
        payload["as_of"] = as_of
        payload["unique_event_evaluation"]["as_of"] = as_of  # type: ignore[index]
        report_path = tmp_path / f"diagnostic-{index}.json"
        report_path.write_text(json.dumps(payload), encoding="utf-8")
        summaries.extend(load_forward_diagnostic_reports([report_path]))
    history = build_forward_diagnostic_history(summaries)
    history_path = tmp_path / "diagnostic-history.json"
    history_path.write_text(json.dumps(history), encoding="utf-8")
    return history_path


def test_history_tool_is_verified_redacted_and_does_not_reopen_sources(tmp_path: Path) -> None:
    history_path = _write_history(tmp_path)
    for source_path in tmp_path.glob("diagnostic-[0-9].json"):
        source_path.unlink()

    toolset = ReadOnlyToolset(
        SnapshotRepository(Path("data/demo/snapshot.json")),
        diagnostic_history_path=history_path,
    )
    history_tool = next(
        item for item in toolset.definitions() if item.name == "get_forward_diagnostic_history"
    )
    assert "independence is not assessed" in history_tool.description
    assert "human review is not recorded" in history_tool.description
    assert "never model-promotion" in history_tool.description
    result = toolset.execute("get_forward_diagnostic_history", {})

    assert result.payload["status"] == "ready"
    assert result.payload["report_count"] == 3
    assert result.payload["snapshot_independence"] == "not_assessed"
    assert result.payload["promotion_eligible"] is False
    assert result.payload["diagnostic_review_required"] is False
    assert result.payload["human_review_status"] == "not_recorded"
    assert result.payload["history_sha256"]
    assert "diagnostic_review_required" in result.citations[0].fields
    assert "human_review_status" in result.citations[0].fields
    assert "snapshot_independence" in result.citations[0].fields
    assert "disclaimer" in result.citations[0].fields
    assert "must-not-leak" not in json.dumps(result.payload)
    assert "private-event" not in json.dumps(result.payload)
    assert result.citations[0].source == "snapshot:forward-diagnostic-history"
    assert result.citations[0].evidence_sha256


@pytest.mark.parametrize("history_contents", ('{"status": "tampered"}', "not-json"))
def test_invalid_history_is_reported_without_raw_error_or_path(
    tmp_path: Path, history_contents: str
) -> None:
    history_path = tmp_path / "diagnostic-history.json"
    history_path.write_text(history_contents, encoding="utf-8")
    toolset = ReadOnlyToolset(
        SnapshotRepository(Path("data/demo/snapshot.json")),
        diagnostic_history_path=history_path,
    )

    result = toolset.execute("get_forward_diagnostic_history", {})

    assert result.payload == {
        "available": False,
        "reason": "diagnostic_history_unavailable_or_invalid",
    }
    assert str(tmp_path) not in json.dumps(result.payload)
