from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class AvailabilityViolation:
    event_id: str
    feature_name: str
    available_at: datetime
    prediction_at: datetime


def audit_feature_availability(frame: pd.DataFrame) -> list[AvailabilityViolation]:
    """Find feature rows whose source timestamp exceeds their prediction cutoff."""
    required = {"event_id", "feature_name", "available_at", "prediction_at"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing availability columns: {sorted(missing)}")
    available = pd.to_datetime(frame["available_at"], utc=True)
    prediction = pd.to_datetime(frame["prediction_at"], utc=True)
    violations = frame.loc[available > prediction]
    return [
        AvailabilityViolation(
            event_id=str(row.event_id),
            feature_name=str(row.feature_name),
            available_at=pd.Timestamp(row.available_at).to_pydatetime(),
            prediction_at=pd.Timestamp(row.prediction_at).to_pydatetime(),
        )
        for row in violations.itertuples(index=False)
    ]


def assert_point_in_time(frame: pd.DataFrame) -> None:
    violations = audit_feature_availability(frame)
    if violations:
        preview = ", ".join(
            f"{item.event_id}:{item.feature_name}" for item in violations[:5]
        )
        raise ValueError(f"Point-in-time audit failed ({len(violations)} rows): {preview}")


def build_market_features(
    prices: pd.DataFrame,
    benchmark_symbol: str = "SPY",
    momentum_windows: tuple[int, ...] = (5, 21, 63, 126, 252),
    volatility_windows: tuple[int, ...] = (21, 63),
    beta_window: int = 252,
) -> pd.DataFrame:
    """Build trailing-only market features from adjusted daily bars."""
    required = {"date", "symbol", "close", "volume"}
    missing = required.difference(prices.columns)
    if missing:
        raise ValueError(f"Missing price columns: {sorted(missing)}")
    frame = prices.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame = frame.sort_values(["symbol", "date"])
    grouped = frame.groupby("symbol", sort=False, group_keys=False)
    frame["return_1d"] = grouped["close"].pct_change(fill_method=None)
    frame["dollar_volume"] = frame["close"] * frame["volume"]
    for window in momentum_windows:
        frame[f"momentum_{window}d"] = grouped["close"].pct_change(
            periods=window, fill_method=None
        )
    for window in volatility_windows:
        frame[f"volatility_{window}d"] = (
            grouped["return_1d"].rolling(window, min_periods=window).std().reset_index(level=0, drop=True)
            * np.sqrt(252)
        )
    frame["median_dollar_volume_60d"] = (
        grouped["dollar_volume"].rolling(60, min_periods=20).median().reset_index(level=0, drop=True)
    )

    benchmark = (
        frame.loc[frame["symbol"] == benchmark_symbol, ["date", "return_1d"]]
        .rename(columns={"return_1d": "benchmark_return"})
        .drop_duplicates("date")
        .sort_values("date")
    )
    benchmark["benchmark_variance"] = benchmark["benchmark_return"].rolling(
        beta_window, min_periods=63
    ).var()
    frame = frame.merge(benchmark, on="date", how="left", validate="many_to_one")
    rolling_cov = (
        frame.groupby("symbol", sort=False)
        .apply(
            lambda group: group["return_1d"].rolling(beta_window, min_periods=63).cov(
                group["benchmark_return"]
            ),
            include_groups=False,
        )
        .reset_index(level=0, drop=True)
    )
    frame["beta_252d"] = rolling_cov / frame["benchmark_variance"].replace(0, np.nan)
    frame = frame.drop(columns="benchmark_variance")
    return frame


def build_dynamic_universe(
    market_features: pd.DataFrame,
    universe_size: int = 1000,
    minimum_price: float = 5.0,
    minimum_history_sessions: int = 252,
) -> pd.DataFrame:
    """Select liquid securities monthly using only data available at selection time."""
    frame = market_features.copy().sort_values(["symbol", "date"])
    frame["history_sessions"] = frame.groupby("symbol").cumcount() + 1
    frame["month"] = frame["date"].dt.to_period("M")
    month_end_rows = frame.groupby(["symbol", "month"], observed=True).tail(1)
    eligible = month_end_rows.loc[
        (month_end_rows["close"] >= minimum_price)
        & (month_end_rows["history_sessions"] >= minimum_history_sessions)
        & month_end_rows["median_dollar_volume_60d"].notna()
    ].copy()
    eligible["liquidity_rank"] = eligible.groupby("month")[
        "median_dollar_volume_60d"
    ].rank(method="first", ascending=False)
    eligible = eligible.loc[eligible["liquidity_rank"] <= universe_size]
    return eligible[["month", "symbol", "liquidity_rank", "median_dollar_volume_60d"]]


def latest_available_facts(facts: pd.DataFrame, cutoff: datetime) -> pd.Series:
    """Return the latest filed value for each XBRL concept as of a cutoff."""
    required = {"concept", "value", "filed_at"}
    missing = required.difference(facts.columns)
    if missing:
        raise ValueError(f"Missing fact columns: {sorted(missing)}")
    frame = facts.copy()
    frame["filed_at"] = pd.to_datetime(frame["filed_at"], utc=True)
    cutoff_ts = pd.Timestamp(cutoff)
    if cutoff_ts.tzinfo is None:
        raise ValueError("cutoff must be timezone-aware")
    eligible = frame.loc[frame["filed_at"] <= cutoff_ts].sort_values("filed_at")
    return eligible.groupby("concept", observed=True).tail(1).set_index("concept")["value"]
