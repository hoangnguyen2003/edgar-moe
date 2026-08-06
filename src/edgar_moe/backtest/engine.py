from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from edgar_moe.backtest.optimizer import allocate_neutral
from edgar_moe.settings import PortfolioConfig


@dataclass(frozen=True)
class BacktestResult:
    daily: pd.DataFrame
    positions: pd.DataFrame


def run_event_backtest(
    signals: pd.DataFrame,
    daily_returns: pd.DataFrame,
    config: PortfolioConfig | None = None,
) -> BacktestResult:
    """Simulate overlapping event signals with daily re-optimization and costs."""
    config = config or PortfolioConfig()
    signal_required = {
        "entry_date",
        "exit_date",
        "security_id",
        "ticker",
        "score",
        "beta",
        "industry_code",
    }
    return_required = {"date", "security_id", "return"}
    if missing := signal_required.difference(signals.columns):
        raise ValueError(f"Missing signal columns: {sorted(missing)}")
    if missing := return_required.difference(daily_returns.columns):
        raise ValueError(f"Missing return columns: {sorted(missing)}")

    signals = signals.copy()
    returns = daily_returns.copy()
    signals["entry_date"] = _normalized_utc_dates(signals["entry_date"])
    signals["exit_date"] = _normalized_utc_dates(signals["exit_date"])
    returns["date"] = _normalized_utc_dates(returns["date"])
    return_matrix = returns.pivot(index="date", columns="security_id", values="return").sort_index()
    has_return_components = {"overnight_return", "intraday_return"}.issubset(returns.columns)
    overnight_matrix = (
        returns.pivot(index="date", columns="security_id", values="overnight_return").sort_index()
        if has_return_components
        else None
    )
    intraday_matrix = (
        returns.pivot(index="date", columns="security_id", values="intraday_return").sort_index()
        if has_return_components
        else None
    )

    previous_weights: dict[str, float] = {}
    daily_rows: list[dict[str, float | pd.Timestamp | str]] = []
    position_rows: list[dict[str, float | pd.Timestamp | str]] = []
    equity = 1.0
    for day, return_row in return_matrix.iterrows():
        active = signals.loc[(signals["entry_date"] <= day) & (signals["exit_date"] >= day)].copy()
        if not active.empty:
            active = active.sort_values("entry_date").groupby("security_id", as_index=False).tail(1)
            weights, diagnostics = allocate_neutral(
                active,
                gross_exposure=config.gross_exposure,
                maximum_net_exposure=config.maximum_net_exposure,
                maximum_beta_exposure=config.maximum_beta_exposure,
                maximum_industry_exposure=config.maximum_industry_exposure,
                maximum_name_weight=config.maximum_name_weight,
                long_quantile=config.long_quantile,
                short_quantile=config.short_quantile,
            )
            current_weights = dict(zip(active["security_id"], weights, strict=True))
        else:
            diagnostics = None
            current_weights = {}

        securities = set(previous_weights) | set(current_weights)
        turnover = 0.5 * sum(
            abs(current_weights.get(security, 0.0) - previous_weights.get(security, 0.0))
            for security in securities
        )
        if overnight_matrix is not None and intraday_matrix is not None:
            overnight_row = overnight_matrix.loc[day]
            intraday_row = intraday_matrix.loc[day]
            overnight_return = sum(
                weight * _finite_return(overnight_row.get(security, 0.0))
                for security, weight in previous_weights.items()
            )
            intraday_return = sum(
                weight * _finite_return(intraday_row.get(security, 0.0))
                for security, weight in current_weights.items()
            )
            gross_return = overnight_return + intraday_return
        else:
            overnight_return = 0.0
            intraday_return = sum(
                weight * _finite_return(return_row.get(security, 0.0))
                for security, weight in current_weights.items()
            )
            gross_return = intraday_return
        transaction_cost = turnover * config.base_transaction_cost_bps / 10_000
        short_exposure = sum(abs(weight) for weight in current_weights.values() if weight < 0)
        borrow_cost = short_exposure * config.base_borrow_cost_annual / 252
        net_return = gross_return - transaction_cost - borrow_cost
        equity *= 1 + net_return
        daily_rows.append(
            {
                "date": day,
                "gross_return": gross_return,
                "overnight_return": overnight_return,
                "intraday_return": intraday_return,
                "transaction_cost": transaction_cost,
                "borrow_cost": borrow_cost,
                "net_return": net_return,
                "equity": equity,
                "turnover": turnover,
                "gross_exposure": diagnostics.gross_exposure if diagnostics else 0.0,
                "net_exposure": diagnostics.net_exposure if diagnostics else 0.0,
                "beta_exposure": diagnostics.beta_exposure if diagnostics else 0.0,
                "solver": diagnostics.solver if diagnostics else "empty",
            }
        )
        if not active.empty:
            for row in active.assign(weight=weights).itertuples(index=False):
                if abs(row.weight) > 1e-12:
                    position_rows.append(
                        {
                            "date": day,
                            "security_id": row.security_id,
                            "ticker": row.ticker,
                            "weight": row.weight,
                            "score": row.score,
                            "beta": row.beta,
                            "industry_code": row.industry_code,
                        }
                    )
        previous_weights = current_weights
    return BacktestResult(pd.DataFrame(daily_rows), pd.DataFrame(position_rows))


def _finite_return(value: object) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(numeric) if pd.notna(numeric) else 0.0


def _normalized_utc_dates(values: pd.Series) -> pd.Series:
    """Return normalized, timezone-naive UTC dates for safe comparisons."""
    return pd.to_datetime(values, utc=True).dt.tz_localize(None).dt.normalize()
