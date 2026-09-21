from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats


def newey_west_t_stat(returns: np.ndarray, lags: int = 5) -> float:
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 3:
        return float("nan")
    centered = values - values.mean()
    gamma_zero = float(np.dot(centered, centered) / len(values))
    long_run_variance = gamma_zero
    for lag in range(1, min(lags, len(values) - 1) + 1):
        weight = 1 - lag / (lags + 1)
        covariance = float(np.dot(centered[lag:], centered[:-lag]) / len(values))
        long_run_variance += 2 * weight * covariance
    standard_error = math.sqrt(max(long_run_variance, 0) / len(values))
    return float(values.mean() / standard_error) if standard_error else float("nan")


def annualized_return(returns: np.ndarray) -> float:
    """Geometric (compounded) annualized return of daily returns."""
    values = np.asarray(returns, dtype=float)
    if not len(values):
        return 0.0
    growth = float(np.prod(1 + values))
    # A total loss cannot be annualized geometrically; report -100%.
    return growth ** (252 / len(values)) - 1 if growth > 0 else -1.0


def annualized_sharpe(returns: np.ndarray) -> float:
    """Geometric annualized return over annualized volatility.

    Both the reported Sharpe ratio and its bootstrap interval use this statistic,
    so the interval describes the same quantity as the point estimate.
    """
    values = np.asarray(returns, dtype=float)
    if len(values) < 2:
        return 0.0
    volatility = float(values.std(ddof=1) * np.sqrt(252))
    return annualized_return(values) / volatility if volatility else 0.0


def performance_metrics(daily: pd.DataFrame, return_column: str = "net_return") -> dict[str, float]:
    returns = pd.to_numeric(daily[return_column], errors="coerce").dropna()
    if returns.empty:
        return {name: 0.0 for name in _metric_names()}
    values = returns.to_numpy(dtype=float)
    annual_return = annualized_return(values)
    annualized_volatility = float(returns.std(ddof=1) * np.sqrt(252))
    sharpe = annualized_sharpe(values)
    # Downside deviation: root mean square of below-zero returns.
    downside = float(np.sqrt(np.mean(np.minimum(values, 0.0) ** 2)) * np.sqrt(252))
    sortino = annual_return / downside if downside else 0.0
    equity = (1 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1
    return {
        "annualized_return": annual_return,
        "annualized_volatility": annualized_volatility,
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "maximum_drawdown": float(drawdown.min()),
        "hit_rate": float((returns > 0).mean()),
        "average_turnover": float(daily.get("turnover", pd.Series(0.0)).mean()),
        "newey_west_t_stat": newey_west_t_stat(returns.to_numpy()),
    }


def predictive_metrics(target: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    target = np.asarray(target, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    valid = np.isfinite(target) & np.isfinite(prediction)
    target = target[valid]
    prediction = prediction[valid]
    if len(target) < 3:
        return {"rmse": float("nan"), "mae": float("nan"), "rank_ic": float("nan")}
    return {
        "rmse": float(np.sqrt(np.mean((target - prediction) ** 2))),
        "mae": float(np.mean(np.abs(target - prediction))),
        "rank_ic": float(stats.spearmanr(target, prediction).statistic),
    }


def block_bootstrap_sharpe_interval(
    returns: np.ndarray,
    block_size: int = 20,
    samples: int = 1000,
    seed: int = 42,
) -> tuple[float, float]:
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < block_size * 2:
        return float("nan"), float("nan")
    generator = np.random.default_rng(seed)
    sharpes = []
    blocks_needed = math.ceil(len(values) / block_size)
    maximum_start = len(values) - block_size
    for _ in range(samples):
        starts = generator.integers(0, maximum_start + 1, size=blocks_needed)
        sample = np.concatenate([values[start : start + block_size] for start in starts])[
            : len(values)
        ]
        sharpes.append(annualized_sharpe(sample))
    low, high = np.quantile(sharpes, [0.025, 0.975])
    return float(low), float(high)


def _metric_names() -> tuple[str, ...]:
    return (
        "annualized_return",
        "annualized_volatility",
        "sharpe",
        "sortino",
        "maximum_drawdown",
        "hit_rate",
        "average_turnover",
        "newey_west_t_stat",
    )
