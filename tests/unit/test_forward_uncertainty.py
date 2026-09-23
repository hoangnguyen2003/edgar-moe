from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta, timezone

import pytest

from edgar_moe.forward.uncertainty import (
    MAXIMUM_PAIRS,
    MINIMUM_NONEMPTY_MONTHS,
    _bootstrap,
    _resample_month_indices,
    clustered_rank_ic_interval,
)


def cohort(
    months: int = 12, per_month: int = 10
) -> tuple[list[float], list[float], list[datetime]]:
    dates = [
        datetime(2025 + month // 12, month % 12 + 1, 10, tzinfo=UTC)
        for month in range(months)
        for _ in range(per_month)
    ]
    scores = [math.sin(index * 0.23) + index * 0.0001 for index in range(len(dates))]
    labels = [score * 0.3 + math.cos(index * 0.51) for index, score in enumerate(scores)]
    return scores, labels, dates


def test_withholds_interval_when_one_month_contains_many_filings() -> None:
    scores, labels, dates = cohort(months=1, per_month=120)
    interval = clustered_rank_ic_interval(scores, labels, dates)
    assert interval.status == "insufficient_months"
    assert interval.calendar_months == 1
    assert (interval.low, interval.high) == (None, None)


def test_requires_minimum_pairs_before_months() -> None:
    scores, labels, dates = cohort(months=12, per_month=2)
    interval = clustered_rank_ic_interval(scores, labels, dates)
    assert interval.status == "insufficient_pairs"
    assert interval.calendar_months == MINIMUM_NONEMPTY_MONTHS


def test_resampling_keeps_every_filing_in_a_month_together() -> None:
    blocks = [(0, 1), (2, 3), (), (4, 5), (6, 7), (8, 9)]
    chosen = _resample_month_indices(blocks, random.Random(11))
    for left, right in ((0, 1), (2, 3), (4, 5), (6, 7), (8, 9)):
        assert chosen.count(left) == chosen.count(right)


def test_months_are_utc_and_empty_calendar_months_keep_their_positions() -> None:
    scores, labels, _ = cohort(months=12)
    spaced = [
        datetime(2025 + month // 6, (month % 6) * 2 + 1, 10, tzinfo=UTC)
        for month in range(12)
        for _ in range(10)
    ]
    assert clustered_rank_ic_interval(scores, labels, spaced).status == "ready"

    local_next_month = datetime(2025, 2, 1, 1, tzinfo=timezone(timedelta(hours=7)))
    one_month = clustered_rank_ic_interval(
        scores, labels, [datetime(2025, 1, 31, tzinfo=UTC)] * 119 + [local_next_month]
    )
    assert one_month.status == "insufficient_months"
    assert one_month.calendar_months == 1


def test_interval_is_deterministic_and_cache_invalidates_on_append() -> None:
    _bootstrap.cache_clear()
    scores, labels, dates = cohort()
    first = clustered_rank_ic_interval(scores, labels, dates)
    assert first.status == "ready"
    assert first.low is not None and first.high is not None
    assert -1 <= first.low <= first.high <= 1
    assert first.block_months == 2
    assert first.resamples == 1000

    second = clustered_rank_ic_interval(scores, labels, dates)
    assert second == first
    assert _bootstrap.cache_info().hits == 1

    newer = clustered_rank_ic_interval(scores + [0.12], labels + [-0.05], dates + [dates[-1]])
    assert newer.status == "ready"
    assert _bootstrap.cache_info().misses == 2


def test_constant_and_invalid_inputs_fail_closed() -> None:
    scores, labels, dates = cohort()
    assert (
        clustered_rank_ic_interval([0.0] * len(scores), labels, dates).status == "undefined_rank_ic"
    )
    with pytest.raises(ValueError, match="align"):
        clustered_rank_ic_interval(scores, labels[:-1], dates)
    with pytest.raises(ValueError, match="finite"):
        clustered_rank_ic_interval(scores[:-1] + [math.nan], labels, dates)


def test_capacity_boundary_is_explicit() -> None:
    scores, labels, dates = cohort(months=12, per_month=MAXIMUM_PAIRS // 12 + 1)
    result = clustered_rank_ic_interval(scores, labels, dates)
    assert result.status == "capacity_review_required"
    assert result.low is None and result.high is None

    small_scores, small_labels, _ = cohort()
    distant_months = [
        datetime(1900 + month * 11, 1, 10, tzinfo=UTC) for month in range(12) for _ in range(10)
    ]
    distant = clustered_rank_ic_interval(small_scores, small_labels, distant_months)
    assert distant.status == "capacity_review_required"
