import numpy as np
import pandas as pd
import pytest

from edgar_moe.backtest.engine import run_event_backtest
from edgar_moe.backtest.metrics import (
    block_bootstrap_sharpe_interval,
    performance_metrics,
    predictive_metrics,
)
from edgar_moe.settings import PortfolioConfig


def test_predictive_and_performance_metrics_are_finite() -> None:
    prediction = np.array([0.2, -0.1, 0.4, 0.05])
    target = np.array([0.3, -0.2, 0.5, 0.0])
    predictive = predictive_metrics(target, prediction)
    assert predictive["rank_ic"] > 0.9
    assert predictive["rmse"] > 0

    daily = pd.DataFrame(
        {
            "net_return": np.tile([0.004, -0.002, 0.003, 0.001], 30),
            "turnover": 0.1,
        }
    )
    performance = performance_metrics(daily)
    assert performance["annualized_return"] > 0
    assert performance["maximum_drawdown"] < 0
    low, high = block_bootstrap_sharpe_interval(
        daily["net_return"].to_numpy(), block_size=10, samples=50
    )
    assert np.isfinite(low)
    assert low <= high


def test_event_backtest_holds_overlapping_signals() -> None:
    securities = ["short", "low", "high", "long"]
    signals = pd.DataFrame(
        {
            "entry_date": pd.Timestamp("2026-01-02"),
            "exit_date": pd.Timestamp("2026-01-06"),
            "security_id": securities,
            "ticker": ["S", "L", "H", "W"],
            "score": [-2.0, -1.0, 1.0, 2.0],
            "beta": 0.0,
            "industry_code": ["a", "b", "c", "d"],
        }
    )
    dates = pd.bdate_range("2026-01-01", "2026-01-07")
    returns = pd.DataFrame(
        [
            {
                "date": day,
                "security_id": security,
                "return": 0.01 if security == "long" else -0.01 if security == "short" else 0.0,
            }
            for day in dates
            for security in securities
        ]
    )
    config = PortfolioConfig(
        maximum_name_weight=0.5,
        maximum_industry_exposure=0.5,
        base_transaction_cost_bps=0,
        base_borrow_cost_annual=0,
    )

    result = run_event_backtest(signals, returns, config)

    assert len(result.daily) == len(dates)
    assert not result.positions.empty
    assert result.daily.loc[
        result.daily["date"] == pd.Timestamp("2026-01-05"), "gross_return"
    ].item() == pytest.approx(0.01)
    assert result.daily.iloc[-1]["gross_exposure"] == 0


def test_backtest_rejects_missing_signal_columns() -> None:
    with pytest.raises(ValueError, match="Missing signal columns"):
        run_event_backtest(pd.DataFrame({"score": [1.0]}), pd.DataFrame())


def test_backtest_uses_previous_weights_overnight_and_current_weights_intraday() -> None:
    securities = ["short", "low", "high", "long"]
    signals = pd.DataFrame(
        {
            "entry_date": pd.Timestamp("2026-01-02"),
            "exit_date": pd.Timestamp("2026-01-05"),
            "security_id": securities,
            "ticker": ["S", "L", "H", "W"],
            "score": [-2.0, -1.0, 1.0, 2.0],
            "beta": 0.0,
            "industry_code": ["a", "b", "c", "d"],
        }
    )
    rows = []
    for day in pd.to_datetime(["2026-01-02", "2026-01-05"]):
        for security in securities:
            overnight = 0.02 if security == "long" else -0.02 if security == "short" else 0.0
            rows.append(
                {
                    "date": day,
                    "security_id": security,
                    "return": overnight,
                    "overnight_return": overnight,
                    "intraday_return": 0.0,
                }
            )
    config = PortfolioConfig(
        maximum_name_weight=0.5,
        maximum_industry_exposure=0.5,
        base_transaction_cost_bps=0,
        base_borrow_cost_annual=0,
    )

    result = run_event_backtest(signals, pd.DataFrame(rows), config)

    assert result.daily.iloc[0]["overnight_return"] == 0.0
    assert result.daily.iloc[1]["overnight_return"] == pytest.approx(0.02)
    assert result.daily.iloc[1]["intraday_return"] == 0.0
