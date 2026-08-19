"""Non-official short-horizon diagnostics for already-recorded forecasts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, cast

import pandas as pd
import pandas_market_calendars as mcal

from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.forward.metrics import forward_metrics

DEFAULT_DIAGNOSTIC_HORIZON_SESSIONS = 5


def diagnostic_report(
    dataset: ResearchDataset,
    forecasts: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime,
    horizon_sessions: int = DEFAULT_DIAGNOSTIC_HORIZON_SESSIONS,
) -> dict[str, Any]:
    """Evaluate short-horizon outcomes without writing to the official registry.

    The production registry's 20-session labels remain the official evaluation. This
    report reuses the immutable pre-entry scores and computes a separate outcome from
    daily return components once a shorter horizon is observable.
    """
    if horizon_sessions < 2:
        raise ValueError("Diagnostic horizon must be at least two sessions")
    if horizon_sessions >= 20:
        raise ValueError("Diagnostic horizon must be shorter than the official 20-session target")
    observed_at = _aware_utc(as_of)
    event_rows = {
        str(row.event_id): row
        for row in dataset.events.itertuples(index=False)
        if hasattr(row, "event_id")
    }
    returns = _normalize_returns(dataset.daily_returns)
    event_horizons = (
        pd.to_datetime(dataset.events["horizon_at"], utc=True).dt.date
        if "horizon_at" in dataset.events
        else pd.Series(dtype="object")
    )
    forecast_horizons: list[date] = []
    for forecast in forecasts:
        horizon_date = _forecast_horizon_date(forecast)
        if horizon_date is not None:
            forecast_horizons.append(horizon_date)
    horizon_dates = [*event_horizons.tolist(), *forecast_horizons]
    schedule = _schedule(returns, end_date=max(horizon_dates, default=None))
    matured: list[dict[str, Any]] = []
    unmatched = 0
    pending_horizons: list[datetime] = []

    for forecast in forecasts:
        event = event_rows.get(str(forecast.get("event_id", "")))
        security_id = str(
            getattr(event, "security_id", "") or forecast.get("security_id", "")
        )
        entry_date = (
            _forecast_entry_date(forecast) if event is None else _event_entry_date(event)
        )
        if not security_id or entry_date is None:
            unmatched += 1
            continue
        horizon_at = _diagnostic_horizon(schedule, entry_date, horizon_sessions)
        if horizon_at is None:
            unmatched += 1
            continue
        if horizon_at > observed_at:
            pending_horizons.append(horizon_at)
            continue
        beta, beta_source = _resolve_beta(
            event,
            returns,
            security_id=security_id,
            entry_date=entry_date,
        )
        target = _abnormal_return(
            returns,
            security_id=security_id,
            beta=beta,
            entry_date=entry_date,
            horizon_date=horizon_at.date(),
            expected_dates=schedule.loc[
                (schedule["session_date"] >= entry_date)
                & (schedule["session_date"] <= horizon_at.date()),
                "session_date",
            ].tolist(),
        )
        if target is None:
            unmatched += 1
            continue
        matured.append(
            {
                "forecast_id": str(forecast.get("forecast_id", "")),
                "event_id": str(forecast.get("event_id", "")),
                "ticker": str(forecast.get("ticker", getattr(event, "ticker", ""))),
                "score": float(forecast.get("score", float("nan"))),
                "rank": float(forecast.get("rank", float("nan"))),
                "beta": beta,
                "beta_source": beta_source,
                "horizon_at": horizon_at,
                "realized_abnormal_return": target,
            }
        )

    matched_count = len(matured) + len(pending_horizons)
    metrics = forward_metrics(
        [float(item["score"]) for item in matured],
        [float(item["realized_abnormal_return"]) for item in matured],
        forecast_count=matched_count,
    )
    if metrics.matured_count:
        status = "ready"
    elif pending_horizons:
        status = "awaiting_maturity"
    else:
        status = "insufficient_coverage"
    return {
        "diagnostic": True,
        "official_horizon_sessions": 20,
        "horizon_sessions": horizon_sessions,
        "as_of": observed_at,
        "status": status,
        "unmatched_count": unmatched,
        "next_maturity_at": min(pending_horizons, default=None),
        "latest_maturity_at": max(pending_horizons, default=None),
        "forecast_count": len(forecasts),
        "matched_count": matched_count,
        "matured_count": metrics.matured_count,
        "pending_count": len(pending_horizons),
        "coverage": metrics.coverage,
        "rank_ic": metrics.rank_ic,
        "rmse": metrics.rmse,
        "mae": metrics.mae,
        "directional_accuracy": metrics.directional_accuracy,
        "observations": matured,
        "disclaimer": (
            "Diagnostic-only short-horizon outcomes. They do not replace, modify, or "
            "contribute to the official 20-session forward evaluation."
        ),
    }


def _normalize_returns(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "security_id", "symbol", "return", "intraday_return"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Dataset daily returns are missing columns: {sorted(missing)}")
    normalized = frame.copy()
    normalized["date"] = pd.to_datetime(normalized["date"], utc=True).dt.date
    return normalized.sort_values(["security_id", "date"])


def _schedule(returns: pd.DataFrame, *, end_date: date | None = None) -> pd.DataFrame:
    dates = returns["date"].dropna()
    if dates.empty:
        return pd.DataFrame(columns=["session_date", "market_close"])
    latest_date = max(dates)
    if end_date is not None:
        latest_date = max(latest_date, end_date)
    schedule = mcal.get_calendar("NYSE").schedule(
        start_date=min(dates),
        end_date=latest_date,
    ).reset_index()
    schedule = schedule.rename(columns={schedule.columns[0]: "session"})
    schedule["session_date"] = pd.to_datetime(schedule["session"]).dt.date
    schedule["market_close"] = pd.to_datetime(schedule["market_close"], utc=True)
    return schedule[["session_date", "market_close"]]


def _event_entry_date(event: Any) -> date:
    value = getattr(event, "entry_date", None)
    if value is None:
        value = event.entry_at
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _forecast_entry_date(forecast: Mapping[str, Any]) -> date | None:
    value = forecast.get("entry_date") or forecast.get("entry_at")
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _forecast_horizon_date(forecast: Mapping[str, Any]) -> date | None:
    value = forecast.get("horizon_at")
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return cast(date, pd.Timestamp(value).date())
    except (TypeError, ValueError):
        return None


def _resolve_beta(
    event: Any | None,
    returns: pd.DataFrame,
    *,
    security_id: str,
    entry_date: date,
) -> tuple[float, str]:
    if event is not None:
        value = float(getattr(event, "beta", float("nan")))
        if math.isfinite(value):
            return value, "event_snapshot"
    return _estimate_beta(returns, security_id=security_id, entry_date=entry_date)


def _estimate_beta(
    returns: pd.DataFrame,
    *,
    security_id: str,
    entry_date: date,
    window: int = 252,
) -> tuple[float, str]:
    asset = returns.loc[
        (returns["security_id"].astype(str).eq(security_id))
        & (returns["date"] < entry_date),
        ["date", "return"],
    ].rename(columns={"return": "asset_return"})
    benchmark = returns.loc[
        (returns["symbol"].astype(str).str.upper().eq("SPY"))
        & (returns["date"] < entry_date),
        ["date", "return"],
    ].rename(columns={"return": "benchmark_return"})
    joined = asset.merge(benchmark, on="date", how="inner").dropna().tail(window)
    if len(joined) < 63:
        return 1.0, "default"
    variance = float(joined["benchmark_return"].var())
    covariance = float(joined["asset_return"].cov(joined["benchmark_return"]))
    beta = covariance / variance if variance > 0 else float("nan")
    return (beta, "daily_returns") if math.isfinite(beta) else (1.0, "default")


def _diagnostic_horizon(
    schedule: pd.DataFrame,
    entry_date: date,
    horizon_sessions: int,
) -> datetime | None:
    dates = schedule["session_date"].tolist()
    try:
        entry_index = dates.index(entry_date)
    except ValueError:
        return None
    horizon_index = entry_index + horizon_sessions - 1
    if horizon_index >= len(schedule):
        return None
    return cast(datetime, schedule.iloc[horizon_index]["market_close"].to_pydatetime())


def _abnormal_return(
    returns: pd.DataFrame,
    *,
    security_id: str,
    beta: float,
    entry_date: date,
    horizon_date: date,
    expected_dates: Sequence[date],
) -> float | None:
    asset = returns.loc[returns["security_id"].astype(str).eq(security_id)].set_index("date")
    benchmark = returns.loc[returns["symbol"].astype(str).str.upper().eq("SPY")].set_index("date")
    if asset.empty or benchmark.empty:
        return None
    asset_growth = _open_to_close_growth(asset, entry_date, horizon_date, expected_dates)
    benchmark_growth = _open_to_close_growth(benchmark, entry_date, horizon_date, expected_dates)
    if asset_growth is None or benchmark_growth is None:
        return None
    return (asset_growth - 1.0) - beta * (benchmark_growth - 1.0)


def _open_to_close_growth(
    rows: pd.DataFrame,
    entry_date: date,
    horizon_date: date,
    expected_dates: Sequence[date],
) -> float | None:
    if entry_date not in rows.index or horizon_date not in rows.index:
        return None
    entry = rows.loc[entry_date]
    if isinstance(entry, pd.DataFrame):
        entry = entry.iloc[-1]
    intraday = float(entry["intraday_return"])
    if not math.isfinite(intraday):
        return None
    expected_later_dates = list(expected_dates)[1:]
    if any(item not in rows.index for item in expected_later_dates):
        return None
    later = rows.loc[expected_later_dates, "return"]
    if later.isna().any():
        return None
    return (1.0 + intraday) * math.prod(1.0 + float(value) for value in later)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Diagnostic as-of must be timezone-aware")
    return value.astimezone(UTC)
