import numpy as np
import pandas as pd
import pytest

from edgar_moe.backtest.engine import run_event_backtest
from edgar_moe.backtest.metrics import (
    annualized_sharpe,
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


def test_bootstrap_interval_uses_the_same_sharpe_as_the_point_estimate() -> None:
    # A drawdown-heavy series where arithmetic and geometric Sharpe differ.
    returns = np.tile([0.03, -0.028, 0.012, -0.015, 0.004], 60)
    point = performance_metrics(pd.DataFrame({"net_return": returns}))["sharpe"]
    arithmetic = returns.mean() / returns.std(ddof=1) * np.sqrt(252)
    assert abs(point - arithmetic) > 0.05

    # Blocks spanning whole periods give every resample the same multiset of
    # returns, so the interval collapses onto whichever estimator it uses.
    low, high = block_bootstrap_sharpe_interval(returns, block_size=10, samples=20)
    assert low == pytest.approx(point) and high == pytest.approx(point)
    assert annualized_sharpe(returns) == pytest.approx(point)


def test_sortino_uses_downside_deviation() -> None:
    returns = np.array([0.02, -0.01, 0.015, -0.03, 0.01, 0.005] * 20)
    metrics = performance_metrics(pd.DataFrame({"net_return": returns}))
    downside = np.sqrt(np.mean(np.minimum(returns, 0.0) ** 2)) * np.sqrt(252)

    assert metrics["sortino"] == pytest.approx(metrics["annualized_return"] / downside)


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


def test_event_backtest_normalizes_timezone_aware_signal_dates() -> None:
    securities = ["short", "low", "high", "long"]
    signals = pd.DataFrame(
        {
            "entry_date": pd.Timestamp("2026-01-02", tz="UTC"),
            "exit_date": pd.Timestamp("2026-01-05", tz="UTC"),
            "security_id": securities,
            "ticker": ["S", "L", "H", "W"],
            "score": [-2.0, -1.0, 1.0, 2.0],
            "beta": 0.0,
            "industry_code": ["a", "b", "c", "d"],
        }
    )
    returns = pd.DataFrame(
        [
            {
                "date": day,
                "security_id": security,
                "return": 0.01 if security == "long" else -0.01 if security == "short" else 0.0,
            }
            for day in pd.bdate_range("2026-01-02", "2026-01-05")
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

    assert not result.positions.empty
    assert result.daily["date"].dt.tz is None
    assert result.daily.iloc[-1]["gross_return"] == pytest.approx(0.01)


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
