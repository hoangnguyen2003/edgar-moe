from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TemporalSplit:
    development: pd.Index
    validation: pd.Index
    test: pd.Index


def chronological_split(
    event_dates: pd.Series,
    development_end: str,
    validation_start: str,
    validation_end: str,
    test_start: str,
    embargo_sessions: int = 20,
) -> TemporalSplit:
    """Create locked chronological splits with a calendar-day conservative embargo."""
    dates = pd.to_datetime(event_dates, utc=True)
    development_end_ts = pd.Timestamp(development_end, tz="UTC")
    validation_start_ts = pd.Timestamp(validation_start, tz="UTC")
    validation_end_ts = pd.Timestamp(validation_end, tz="UTC")
    test_start_ts = pd.Timestamp(test_start, tz="UTC")
    embargo = pd.Timedelta(days=int(embargo_sessions * 1.6) + 2)

    development = dates.index[dates <= development_end_ts - embargo]
    validation = dates.index[
        (dates >= validation_start_ts) & (dates <= validation_end_ts - embargo)
    ]
    test = dates.index[dates >= test_start_ts]
    if not (len(development) and len(validation) and len(test)):
        raise ValueError("Each temporal split must contain observations")
    return TemporalSplit(development, validation, test)
