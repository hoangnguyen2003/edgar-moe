"""Offline screen-input regressions for issue #292; no provider requests."""

from datetime import date

import pytest

from edgar_moe.data.refresh import UniverseMember
from edgar_moe.data.universe import screen_liquid_universe


def _screen(rows, *, minimum_sessions=2, as_of=date(2022, 12, 31)):
    return screen_liquid_universe(
        [UniverseMember(cik="1", symbol="AAA")],
        {"AAA": rows},
        candidate_count=1,
        minimum_sessions=minimum_sessions,
        minimum_price=1,
        as_of=as_of,
    )


@pytest.mark.parametrize("as_of", [date(2022, 12, 31), None])
@pytest.mark.parametrize(
    "second",
    [
        {"t": "2022-12-29", "c": 10, "v": 100},
        {"t": "2022-12-29", "c": 999, "v": 999999},
        {"t": "2022-12-29T05:00:00Z", "c": 10, "v": 100},
        {"timestamp": "2022-12-29T05:00:00Z", "close": 10, "volume": 100},
        {"t": "2022-12-29", "c": None, "v": 100},
    ],
)
def test_daily_screen_rejects_duplicate_session_even_if_rows_differ(second, as_of):
    with pytest.raises(ValueError, match="duplicate daily session"):
        _screen([{"t": "2022-12-29", "c": 10, "v": 100}, second], as_of=as_of)


@pytest.mark.parametrize("as_of", [date(2022, 12, 31), None])
def test_daily_screen_rejects_invalid_nonempty_session_date(as_of):
    with pytest.raises(ValueError, match="invalid date"):
        _screen(
            [
                {"t": "not-a-date", "c": 10, "v": 100},
                {"t": "2022-12-29", "c": 10, "v": 100},
            ],
            as_of=as_of,
        )


@pytest.mark.parametrize("value", [1e308, 1e154])
def test_daily_screen_rejects_overflowed_dollar_volume(value):
    with pytest.raises(ValueError, match="non-finite dollar volume"):
        _screen(
            [
                {"t": "2022-12-28", "c": value, "v": value},
                {"t": "2022-12-29", "c": value, "v": value},
            ]
        )


def test_unique_daily_sessions_are_order_invariant():
    rows = [
        {"t": "2022-12-29T05:00:00Z", "c": 12, "v": 100},
        {"t": "2022-12-28T05:00:00Z", "c": 10, "v": 100},
    ]
    selected, audit = _screen(rows)
    assert selected[0]["screen_sessions"] == 2
    assert selected[0]["screen_last_price"] == 12
    assert selected[0]["screen_median_dollar_volume"] == 1100
    assert (selected, audit) == _screen(list(reversed(rows)))


def test_missing_price_on_distinct_day_remains_a_coverage_exclusion():
    selected, audit = _screen(
        [
            {"t": "2022-12-28", "c": None, "v": 100},
            {"t": "2022-12-29", "c": 10, "v": 100},
        ]
    )
    assert selected == []
    assert audit["insufficient_sessions"] == 1
