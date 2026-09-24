from datetime import UTC, datetime

import pandas as pd
import pytest

from edgar_moe.features.dataset import (
    _event_timing,
    _indexed_macro_groups,
    _indexed_market_groups,
    _latest_market_row,
    _regime_vector,
)


@pytest.mark.parametrize("unit", ["us", "ns"])
def test_event_timing_respects_timestamp_unit(unit: str) -> None:
    opens = pd.to_datetime(
        pd.Series(["2025-06-03T13:30:00Z", "2025-06-04T13:30:00Z"]), utc=True
    ).dt.as_unit(unit)
    closes = pd.to_datetime(
        pd.Series(["2025-06-03T20:00:00Z", "2025-06-04T20:00:00Z"]), utc=True
    ).dt.as_unit(unit)
    schedule = pd.DataFrame({"market_open": opens, "market_close": closes})

    assert _event_timing(datetime(2025, 6, 2, 21, 30, tzinfo=UTC), schedule, 2) == (
        datetime(2025, 6, 3, 13, 30, tzinfo=UTC),
        datetime(2025, 6, 4, 20, tzinfo=UTC),
    )


@pytest.mark.parametrize("unit", ["us", "ns"])
def test_market_lookup_respects_timestamp_unit(unit: str) -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["TEST", "TEST"],
            "available_at": pd.to_datetime(
                pd.Series(["2025-06-01T20:00:00Z", "2025-06-03T20:00:00Z"]), utc=True
            ).dt.as_unit(unit),
            "close": [100.0, 110.0],
        }
    )

    row = _latest_market_row(
        _indexed_market_groups(frame)["TEST"], datetime(2025, 6, 2, tzinfo=UTC)
    )

    assert row is not None
    assert row["close"] == 100.0


@pytest.mark.parametrize("unit", ["us", "ns"])
def test_macro_lookup_respects_timestamp_unit(unit: str) -> None:
    frame = pd.DataFrame(
        {
            "available_at": pd.to_datetime(
                pd.Series(["2025-06-01T00:00:00Z", "2025-06-03T00:00:00Z"]), utc=True
            ).dt.as_unit(unit),
            "value": [1.0, 2.0],
        }
    )
    spy_row = pd.Series(
        {
            "momentum_21d": 0.1,
            "volatility_21d": 0.2,
            "available_at": pd.Timestamp("2025-06-01T20:00:00Z"),
        }
    )

    values, availability = _regime_vector(
        _indexed_macro_groups({"DFF": frame}),
        datetime(2025, 6, 2, tzinfo=UTC),
        spy_row,
        ["DFF"],
    )

    assert values[0] == 1.0
    assert availability[0] == datetime(2025, 6, 1, tzinfo=UTC)
