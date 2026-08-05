from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from edgar_moe.features.point_in_time import (
    assert_point_in_time,
    build_market_features,
    latest_available_facts,
)


def test_availability_audit_rejects_future_row() -> None:
    frame = pd.DataFrame(
        [
            {
                "event_id": "one",
                "feature_name": "safe",
                "available_at": "2026-01-01T10:00:00Z",
                "prediction_at": "2026-01-01T11:00:00Z",
            },
            {
                "event_id": "two",
                "feature_name": "leak",
                "available_at": "2026-01-02T10:00:00Z",
                "prediction_at": "2026-01-01T11:00:00Z",
            },
        ]
    )
    with pytest.raises(ValueError, match="two:leak"):
        assert_point_in_time(frame)


def test_latest_facts_obeys_cutoff() -> None:
    facts = pd.DataFrame(
        [
            {"concept": "Revenue", "value": 10, "filed_at": "2025-01-01T00:00:00Z"},
            {"concept": "Revenue", "value": 20, "filed_at": "2026-01-01T00:00:00Z"},
        ]
    )
    result = latest_available_facts(facts, datetime(2025, 6, 1, tzinfo=UTC))
    assert result["Revenue"] == 10


def test_market_beta_uses_benchmark_variance_by_date() -> None:
    dates = pd.bdate_range("2025-01-01", periods=90, tz="UTC")
    benchmark_returns = 0.004 * np.sin(np.arange(len(dates)) / 3)
    asset_returns = 2 * benchmark_returns
    frame = pd.concat(
        [
            pd.DataFrame(
                {
                    "date": dates,
                    "symbol": "SPY",
                    "close": 100 * np.cumprod(1 + benchmark_returns),
                    "volume": 1_000_000,
                }
            ),
            pd.DataFrame(
                {
                    "date": dates,
                    "symbol": "TEST",
                    "close": 50 * np.cumprod(1 + asset_returns),
                    "volume": 500_000,
                }
            ),
        ],
        ignore_index=True,
    )

    features = build_market_features(
        frame,
        momentum_windows=(5,),
        volatility_windows=(21,),
        beta_window=63,
    )
    beta = features.loc[features["symbol"] == "TEST", "beta_252d"].dropna().iloc[-1]
    assert beta == pytest.approx(2.0, rel=0.01)
