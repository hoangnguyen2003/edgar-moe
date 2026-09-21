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
        "model_id": "model-1",
        "forecast_as_of": "2026-08-03T10:00:00Z",
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
        (1.01 * 1.02 * 1.01 * 1.0 * 1.03 - 1.0) - (1.005 * 1.01 * 1.0 * 1.01 * 1.005 - 1.0)
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


def test_missing_benchmark_is_reported_as_data_gap_not_waiting() -> None:
    dataset = diagnostic_fixture()
    dataset.daily_returns = dataset.daily_returns.query("symbol != 'SPY'")
    future = {**forecast(), "event_id": "future", "entry_date": "2026-08-10"}
    report = diagnostic_report(
        dataset, [forecast(), future], as_of=datetime(2026, 8, 8, tzinfo=UTC)
    )
    assert report["status"] == "insufficient_coverage"
    assert report["pending_count"] == 1
    assert report["unmatched_reasons"] == {"missing_benchmark_returns": 1}
    assert report["unmatched_forecasts"][0]["forecast_id"] == "forecast-1"


def test_coverage_includes_unmatched_forecasts_in_denominator() -> None:
    missing = {**forecast(), "event_id": "missing", "security_id": "missing"}
    report = diagnostic_report(
        diagnostic_fixture(),
        [forecast(), missing],
        as_of=datetime(2026, 8, 8, tzinfo=UTC),
    )
    assert report["matured_count"] == 1
    assert report["unmatched_count"] == 1
    assert report["coverage"] == 0.5
    assert report["unmatched_reasons"] == {"missing_or_invalid_horizon_returns": 1}


def test_unique_events_select_earliest_forecast_independent_of_input_order() -> None:
    early = forecast()
    later = {
        **early,
        "forecast_id": "forecast-2",
        "score": -0.8,
        "forecast_as_of": "2026-08-03T12:00:00Z",
    }
    other_model = {**early, "model_id": "model-2", "forecast_id": "forecast-3"}
    reports = [
        diagnostic_report(diagnostic_fixture(), rows, as_of=datetime(2026, 8, 8, tzinfo=UTC))
        for rows in ([later, other_model, early], [early, other_model, later])
    ]
    unique = reports[0]["unique_event_evaluation"]
    assert unique == reports[1]["unique_event_evaluation"]
    assert reports[0]["matured_count"] == 3
    assert unique["matured_count"] == 2
    assert unique["event_count"] == 2
    assert unique["repeated_forecast_count"] == 1
    assert unique["selected_forecast_ids"] == ["forecast-1", "forecast-3"]
    assert unique["observations"][0]["score"] == early["score"]


def test_unique_events_do_not_replace_unmatched_first_forecast() -> None:
    early = {**forecast(), "event_id": "absent", "security_id": "absent"}
    later = {
        **early,
        "forecast_id": "later",
        "security_id": "asset-1",
        "forecast_as_of": "2026-08-03T12:00:00Z",
    }
    report = diagnostic_report(
        diagnostic_fixture(), [later, early], as_of=datetime(2026, 8, 8, tzinfo=UTC)
    )
    assert report["matured_count"] == 1
    unique = report["unique_event_evaluation"]
    assert unique["matured_count"] == 0
    assert unique["unmatched_count"] == 1
    assert unique["coverage"] == 0.0


def test_unique_events_tie_break_and_missing_timestamp() -> None:
    first = forecast()
    tied = {**first, "forecast_id": "forecast-0"}
    report = diagnostic_report(
        diagnostic_fixture(), [first, tied], as_of=datetime(2026, 8, 8, tzinfo=UTC)
    )
    assert report["unique_event_evaluation"]["selected_forecast_ids"] == ["forecast-0"]
    for timestamp in (None, "2026-08-03T10:00:00", "invalid"):
        report = diagnostic_report(
            diagnostic_fixture(),
            [{**first, "forecast_as_of": timestamp}],
            as_of=datetime(2026, 8, 8, tzinfo=UTC),
        )
        assert report["unique_event_evaluation"]["status"] == "unavailable"
