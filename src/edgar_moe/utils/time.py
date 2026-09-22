from __future__ import annotations

from datetime import date, datetime, time
from typing import cast
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal

NEW_YORK = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def ensure_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime and reject ambiguous naive timestamps."""
    if value.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware")
    return value.astimezone(UTC)


def next_nyse_open(accepted_at: datetime) -> datetime:
    """Return the first NYSE session open strictly after a filing acceptance time."""
    accepted_at = ensure_utc(accepted_at)
    calendar = mcal.get_calendar("NYSE")
    local_date = accepted_at.astimezone(NEW_YORK).date()
    schedule = calendar.schedule(
        start_date=local_date,
        end_date=local_date + pd.Timedelta(days=10),
    )
    for market_open in schedule["market_open"]:
        candidate = market_open.to_pydatetime().astimezone(UTC)
        if candidate > accepted_at:
            return cast(datetime, candidate)
    raise RuntimeError("Unable to find a following NYSE session")


def target_nyse_open(observed_at: datetime) -> datetime:
    """Return the current-session open, or the next open on non-session days.

    A run recorded after a trading day's open has already missed that day's
    entry point. Returning that same open lets callers represent the missed
    margin as zero instead of incorrectly measuring to the following session.
    """
    observed_at = ensure_utc(observed_at)
    calendar = mcal.get_calendar("NYSE")
    local_date = observed_at.astimezone(NEW_YORK).date()
    schedule = calendar.schedule(
        start_date=local_date,
        end_date=local_date + pd.Timedelta(days=10),
    )
    for market_open in schedule["market_open"]:
        candidate = market_open.to_pydatetime().astimezone(UTC)
        if candidate.astimezone(NEW_YORK).date() == local_date:
            return cast(datetime, candidate)
        if candidate > observed_at:
            return cast(datetime, candidate)
    raise RuntimeError("Unable to find a target NYSE session")


def session_close_after(entry_at: datetime, sessions: int) -> datetime:
    """Return the close of the Nth NYSE session beginning at entry_at's session."""
    if sessions < 1:
        raise ValueError("sessions must be positive")
    entry_at = ensure_utc(entry_at)
    start = entry_at.astimezone(NEW_YORK).date()
    calendar = mcal.get_calendar("NYSE")
    schedule = calendar.schedule(start_date=start, end_date=start + pd.Timedelta(days=sessions * 3))
    eligible = schedule[schedule["market_open"] >= pd.Timestamp(entry_at)]
    if len(eligible) < sessions:
        raise RuntimeError("Unable to find enough NYSE sessions")
    close = eligible.iloc[sessions - 1]["market_close"].to_pydatetime().astimezone(UTC)
    return cast(datetime, close)


def utc_close(day: date) -> datetime:
    """Convenience helper used by deterministic demo fixtures."""
    return datetime.combine(day, time(16, 0), tzinfo=NEW_YORK).astimezone(UTC)
