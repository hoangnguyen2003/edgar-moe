from datetime import UTC, date, datetime

import pandas as pd
import pytest

from edgar_moe.forward.workflow import _pre_open_schedule_margin_check
from edgar_moe.modeling.split import chronological_split
from edgar_moe.utils.time import ensure_utc, next_nyse_open, session_close_after, utc_close


def test_chronological_split_keeps_time_order_and_embargo() -> None:
    dates = pd.Series(pd.date_range("2021-01-01", "2026-01-01", freq="30D", tz="UTC"))
    split = chronological_split(
        dates,
        development_end="2022-12-31",
        validation_start="2023-01-01",
        validation_end="2024-12-31",
        test_start="2025-01-01",
        embargo_sessions=20,
    )
    assert dates.loc[split.development].max() < dates.loc[split.validation].min()
    assert dates.loc[split.validation].max() < dates.loc[split.test].min()


def test_exchange_calendar_helpers_obey_sessions() -> None:
    accepted_after_friday_close = datetime(2026, 1, 2, 22, tzinfo=UTC)
    monday_open = next_nyse_open(accepted_after_friday_close)
    assert monday_open == datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    assert session_close_after(monday_open, 2) == datetime(2026, 1, 6, 21, tzinfo=UTC)
    assert utc_close(date(2026, 1, 5)) == datetime(2026, 1, 5, 21, tzinfo=UTC)


def test_pre_open_schedule_margin_warns_when_the_runner_is_late() -> None:
    before_open = datetime(2026, 1, 5, 13, 15, tzinfo=UTC)
    check = _pre_open_schedule_margin_check(before_open)

    assert check.name == "pre_open_schedule_margin"
    assert check.status == "warning"
    assert check.observed_value == 75 * 60
    assert check.threshold == 90 * 60
    assert check.details["target_regular_open"] == "2026-01-05T14:30:00+00:00"
    assert check.details["schedule_phase"] == "before_open"


def test_pre_open_schedule_margin_passes_with_safe_time() -> None:
    before_open = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    check = _pre_open_schedule_margin_check(before_open)

    assert check.status == "passed"
    assert check.observed_value == 150 * 60


def test_pre_open_schedule_margin_warns_after_the_session_open() -> None:
    after_open = datetime(2026, 1, 5, 14, 31, tzinfo=UTC)
    check = _pre_open_schedule_margin_check(after_open)

    assert check.status == "warning"
    assert check.observed_value == 0.0
    assert check.details["target_regular_open"] == "2026-01-05T14:30:00+00:00"
    assert check.details["schedule_phase"] == "entry_already_open"


def test_ensure_utc_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ensure_utc(datetime(2026, 1, 1))
