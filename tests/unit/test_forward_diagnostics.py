from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from edgar_moe.forward.diagnostics import diagnostic_report


def diagnostic_fixture() -> SimpleNamespace:
    dates = pd.date_range("2026-08-03", periods=5, freq="D")
    rows: list[dict[str, object]] = []
    asset_returns = [0.01, 0.02, 0.01, 0.0, 0.03]
    benchmark_returns = [0.005, 0.01, 0.0, 0.01, 0.005]
    for index, timestamp in enumerate(dates):
        date = timestamp.date()
        rows.extend(
            [
                {
                    "date": date,
                    "security_id": "asset-1",
                    "symbol": "TEST",
                    "return": asset_returns[index],
                    "intraday_return": 0.01 if index == 0 else 0.0,
                },
                {
                    "date": date,
                    "security_id": "benchmark",
                    "symbol": "SPY",
                    "return": benchmark_returns[index],
                    "intraday_return": 0.005 if index == 0 else 0.0,
                },
            ]
        )
    events = pd.DataFrame(
        [
            {
                "event_id": "event-1",
                "security_id": "asset-1",
                "ticker": "TEST",
                "entry_date": dates[0].date(),
                "horizon_at": pd.Timestamp("2026-08-28", tz="UTC"),
                "beta": 1.0,
            }
        ]
    )
    return SimpleNamespace(events=events, daily_returns=pd.DataFrame(rows))


def forecast() -> dict[str, object]:
    return {
        "forecast_id": "forecast-1",
        "event_id": "event-1",
        "security_id": "asset-1",
        "ticker": "TEST",
        "entry_date": "2026-08-03",
        "horizon_at": "2026-08-28T20:00:00Z",
        "score": 0.12,
        "rank": 0.9,
    }


def test_diagnostic_report_computes_matured_short_horizon() -> None:
    report = diagnostic_report(
        diagnostic_fixture(),
        [forecast()],
        as_of=datetime(2026, 8, 8, tzinfo=UTC),
    )

    assert report["status"] == "ready"
    assert report["horizon_sessions"] == 5
    assert report["matured_count"] == 1
    assert report["pending_count"] == 0
    assert report["coverage"] == 1.0
    assert report["observations"][0]["realized_abnormal_return"] == pytest.approx(
        (1.01 * 1.02 * 1.01 * 1.0 * 1.03 - 1.0)
        - (1.005 * 1.01 * 1.0 * 1.01 * 1.005 - 1.0)
    )


def test_diagnostic_uses_registry_metadata_when_event_is_not_in_latest_dataset() -> None:
    dataset = diagnostic_fixture()
    dataset.events = dataset.events.iloc[0:0].copy()
    report = diagnostic_report(
        dataset,
        [forecast()],
        as_of=datetime(2026, 8, 8, tzinfo=UTC),
    )

    assert report["status"] == "ready"
    assert report["forecast_count"] == 1
    assert report["matched_count"] == 1
    assert report["unmatched_count"] == 0
    assert report["matured_count"] == 1
    assert report["observations"][0]["beta_source"] == "default"


def test_diagnostic_report_keeps_unmatured_outcomes_pending() -> None:
    report = diagnostic_report(
        diagnostic_fixture(),
        [forecast()],
        as_of=datetime(2026, 8, 7, 19, 59, tzinfo=UTC),
    )

    assert report["status"] == "awaiting_maturity"
    assert report["matured_count"] == 0
    assert report["pending_count"] == 1
    assert report["next_maturity_at"] is not None


def test_diagnostic_report_rejects_non_short_horizon() -> None:
    with pytest.raises(ValueError, match="shorter than the official"):
        diagnostic_report(
            diagnostic_fixture(),
            [forecast()],
            as_of=datetime(2026, 8, 8, tzinfo=UTC),
            horizon_sessions=20,
        )
