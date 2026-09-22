from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

#: Confidence level of the reported rank-IC interval.
RANK_IC_CONFIDENCE = 0.95
#: Two-sided normal quantile for ``RANK_IC_CONFIDENCE``.
_RANK_IC_Z = 1.959964
#: Fisher's transform is undefined at the ends; below this many pairs the
#: Bonett-Wright standard error has no degrees of freedom left either.
_RANK_IC_MINIMUM_PAIRS = 4


@dataclass(frozen=True)
class ForwardMetrics:
    forecast_count: int
    matured_count: int
    pending_count: int
    coverage: float
    rank_ic: float | None
    #: Bounds of the rank IC's confidence interval, or ``None`` when too few
    #: outcomes have settled to state one.
    rank_ic_low: float | None
    rank_ic_high: float | None
    rmse: float | None
    mae: float | None
    directional_accuracy: float | None


def forward_metrics(
    scores: Sequence[float],
    labels: Sequence[float],
    *,
    forecast_count: int,
) -> ForwardMetrics:
    """Score every settled forecast as one pooled sample.

    ``rank_ic`` is a single Spearman correlation over all settled pairs rather
    than the mean of per-run cross-sectional correlations. A run scores the
    filings accepted since the previous one - one to four in production - which
    is too few for a cross-section, so averaging per-run values would dress
    noise in the name of an information coefficient. The cost is that the
    figure mixes ordering within a batch with variation between periods.
    """

    if len(scores) != len(labels):
        raise ValueError("Scores and labels must have the same length")
    paired = [
        (float(score), float(label))
        for score, label in zip(scores, labels, strict=True)
        if math.isfinite(float(score)) and math.isfinite(float(label))
    ]
    matured_count = len(paired)
    pending_count = max(forecast_count - matured_count, 0)
    coverage = matured_count / forecast_count if forecast_count else 0.0
    if not paired:
        return ForwardMetrics(
            forecast_count=forecast_count,
            matured_count=0,
            pending_count=pending_count,
            coverage=coverage,
            rank_ic=None,
            rank_ic_low=None,
            rank_ic_high=None,
            rmse=None,
            mae=None,
            directional_accuracy=None,
        )
    score_values, label_values = zip(*paired, strict=True)
    errors = [score - label for score, label in paired]
    rank_ic = _spearman(score_values, label_values)
    rank_ic_low, rank_ic_high = rank_ic_interval(rank_ic, matured_count)
    return ForwardMetrics(
        forecast_count=forecast_count,
        matured_count=matured_count,
        pending_count=pending_count,
        coverage=coverage,
        rank_ic=rank_ic,
        rank_ic_low=rank_ic_low,
        rank_ic_high=rank_ic_high,
        rmse=math.sqrt(sum(error * error for error in errors) / matured_count),
        mae=sum(abs(error) for error in errors) / matured_count,
        directional_accuracy=sum((score >= 0) == (label >= 0) for score, label in paired)
        / matured_count,
    )


def rank_ic_interval(
    rank_ic: float | None,
    pair_count: int,
) -> tuple[float | None, float | None]:
    """Confidence interval for a Spearman rank IC, by Fisher's transform.

    Uses the Bonett-Wright standard error for rank correlation,
    ``sqrt((1 + rho**2 / 2) / (n - 3))``, transformed back through ``tanh``, so
    the interval stays inside [-1, 1] and is asymmetric near the ends.

    The interval treats the settled forecasts as independent. Forecasts from
    one run share a trading day, so their outcomes are cross-correlated and a
    true interval is wider than this one. It is reported to show how little a
    handful of outcomes can settle, not to claim significance.
    """

    if rank_ic is None or pair_count < _RANK_IC_MINIMUM_PAIRS:
        return None, None
    if abs(rank_ic) >= 1.0:
        # Fisher's transform diverges at the ends; a perfect rank correlation
        # over this sample has no room left to move.
        return rank_ic, rank_ic
    standard_error = math.sqrt((1.0 + rank_ic**2 / 2.0) / (pair_count - 3))
    centre = math.atanh(rank_ic)
    margin = _RANK_IC_Z * standard_error
    return math.tanh(centre - margin), math.tanh(centre + margin)


def percentile_ranks(values: Sequence[float]) -> list[float]:
    """Average-tie percentile ranks in (0, 1], matching pandas rank(pct=True)."""
    if not values:
        return []
    ranks = _average_ranks([float(value) for value in values])
    count = len(ranks)
    return [rank / count for rank in ranks]


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2:
        return None
    return _pearson(_average_ranks(left), _average_ranks(right))


def _average_ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
            end += 1
        average = ((cursor + 1) + end) / 2.0
        for position in range(cursor, end):
            ranks[ordered[position][0]] = average
        cursor = end
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    left_scale = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_scale = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    denominator = left_scale * right_scale
    return numerator / denominator if denominator else None
