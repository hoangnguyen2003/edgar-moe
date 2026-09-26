"""Bounded calendar-cluster uncertainty for prospective rank IC.

The API's point estimate remains the pooled Spearman IC. This module only
resamples immutable, settled score/outcome pairs after enough distinct filing
months exist to make a time-block interval interpretable.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Literal

from edgar_moe.forward.metrics import _spearman

BLOCK_MONTHS = 2
BOOTSTRAP_SAMPLES = 1000
MINIMUM_PAIRS = 100
MINIMUM_NONEMPTY_MONTHS = 12
MAXIMUM_PAIRS = 5000
MAXIMUM_CALENDAR_SPAN_MONTHS = 120
BOOTSTRAP_SEED = 20260923

IntervalStatus = Literal[
    "ready",
    "insufficient_pairs",
    "insufficient_months",
    "undefined_rank_ic",
    "degenerate_resamples",
    "capacity_review_required",
]


@dataclass(frozen=True)
class ClusteredRankIcInterval:
    low: float | None
    high: float | None
    status: IntervalStatus
    calendar_months: int
    block_months: int = BLOCK_MONTHS
    resamples: int = BOOTSTRAP_SAMPLES


def clustered_rank_ic_interval(
    scores: list[float], labels: list[float], accepted_at: list[datetime]
) -> ClusteredRankIcInterval:
    """Percentile interval from overlapping two-calendar-month moving blocks.

    All filings in a month move together. Empty calendar months retain their
    place in the sequence, and the last sampled block is truncated to the
    observed span. A bounded process cache avoids repeating work on unchanged
    append-only labels during a warm serverless instance's lifetime.
    """
    if not (len(scores) == len(labels) == len(accepted_at)):
        raise ValueError("scores, labels, and acceptance times must align")
    if not all(
        math.isfinite(score) and math.isfinite(label)
        for score, label in zip(scores, labels, strict=True)
    ):
        raise ValueError("settled scores and labels must be finite")
    months = tuple(_utc_month(value) for value in accepted_at)
    nonempty_months = len(set(months))
    unavailable = dict(calendar_months=nonempty_months)
    if len(scores) < MINIMUM_PAIRS:
        return ClusteredRankIcInterval(None, None, "insufficient_pairs", **unavailable)
    if nonempty_months < MINIMUM_NONEMPTY_MONTHS:
        return ClusteredRankIcInterval(None, None, "insufficient_months", **unavailable)
    if len(scores) > MAXIMUM_PAIRS or max(months) - min(months) + 1 > MAXIMUM_CALENDAR_SPAN_MONTHS:
        return ClusteredRankIcInterval(None, None, "capacity_review_required", **unavailable)
    if _spearman(scores, labels) is None:
        return ClusteredRankIcInterval(None, None, "undefined_rank_ic", **unavailable)
    low, high, valid_draws = _bootstrap(tuple(scores), tuple(labels), months)
    if valid_draws < int(BOOTSTRAP_SAMPLES * 0.95):
        return ClusteredRankIcInterval(None, None, "degenerate_resamples", **unavailable)
    return ClusteredRankIcInterval(low, high, "ready", **unavailable)


def _utc_month(value: datetime) -> int:
    if not isinstance(value, datetime):
        raise ValueError("acceptance times must be datetimes")
    utc = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc.year * 12 + utc.month - 1


@lru_cache(maxsize=8)
def _bootstrap(
    scores: tuple[float, ...], labels: tuple[float, ...], months: tuple[int, ...]
) -> tuple[float | None, float | None, int]:
    first, last = min(months), max(months)
    blocks = [
        tuple(index for index, month in enumerate(months) if month == period)
        for period in range(first, last + 1)
    ]
    # Resampling duplicates whole months, but does not change the ordering of
    # *distinct* score or label values. Sort/tie-group each vector once rather
    # than sorting the expanded observations for every bootstrap draw.
    score_groups = _ordered_tie_groups(scores)
    label_groups = _ordered_tie_groups(labels)
    rng = random.Random(BOOTSTRAP_SEED)
    replicates: list[float] = []
    for _ in range(BOOTSTRAP_SAMPLES):
        chosen = _resample_month_indices(blocks, rng)
        multiplicities = [0] * len(scores)
        for index in chosen:
            multiplicities[index] += 1
        result = _weighted_spearman(score_groups, label_groups, multiplicities)
        if result is not None:
            replicates.append(max(-1.0, min(1.0, result)))
    if len(replicates) < int(BOOTSTRAP_SAMPLES * 0.95):
        return None, None, len(replicates)
    replicates.sort()
    return _percentile(replicates, 0.025), _percentile(replicates, 0.975), len(replicates)


def _ordered_tie_groups(values: tuple[float, ...]) -> list[tuple[int, ...]]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    groups: list[tuple[int, ...]] = []
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[cursor]]:
            end += 1
        groups.append(tuple(ordered[cursor:end]))
        cursor = end
    return groups


def _weighted_ranks(groups: list[tuple[int, ...]], multiplicities: list[int]) -> list[float]:
    ranks = [0.0] * len(multiplicities)
    preceding = 0
    for group in groups:
        if len(group) == 1:
            index = group[0]
            size = multiplicities[index]
            if size:
                ranks[index] = (2 * preceding + size + 1) / 2.0
                preceding += size
            continue
        size = sum(multiplicities[index] for index in group)
        if size:
            average = (2 * preceding + size + 1) / 2.0
            for index in group:
                ranks[index] = average
            preceding += size
    return ranks


def _weighted_spearman(
    score_groups: list[tuple[int, ...]],
    label_groups: list[tuple[int, ...]],
    multiplicities: list[int],
) -> float | None:
    count = sum(multiplicities)
    if count < 2:
        return None
    score_ranks = _weighted_ranks(score_groups, multiplicities)
    label_ranks = _weighted_ranks(label_groups, multiplicities)
    mean = (count + 1) / 2.0
    numerator = sum(
        weight * (score - mean) * (label - mean)
        for score, label, weight in zip(score_ranks, label_ranks, multiplicities, strict=True)
    )
    score_scale = sum(
        weight * (score - mean) ** 2
        for score, weight in zip(score_ranks, multiplicities, strict=True)
    )
    label_scale = sum(
        weight * (label - mean) ** 2
        for label, weight in zip(label_ranks, multiplicities, strict=True)
    )
    denominator = math.sqrt(score_scale * label_scale)
    return numerator / denominator if denominator else None


def _resample_month_indices(blocks: list[tuple[int, ...]], rng: random.Random) -> list[int]:
    chosen: list[int] = []
    remaining = len(blocks)
    while remaining:
        start = rng.randrange(len(blocks) - BLOCK_MONTHS + 1)
        take = min(BLOCK_MONTHS, remaining)
        for group in blocks[start : start + take]:
            chosen.extend(group)
        remaining -= take
    return chosen


def _percentile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
