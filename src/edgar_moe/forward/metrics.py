from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ForwardMetrics:
    forecast_count: int
    matured_count: int
    pending_count: int
    coverage: float
    rank_ic: float | None
    rmse: float | None
    mae: float | None
    directional_accuracy: float | None


def forward_metrics(
    scores: Sequence[float],
    labels: Sequence[float],
    *,
    forecast_count: int,
) -> ForwardMetrics:
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
            rmse=None,
            mae=None,
            directional_accuracy=None,
        )
    score_values, label_values = zip(*paired, strict=True)
    errors = [score - label for score, label in paired]
    return ForwardMetrics(
        forecast_count=forecast_count,
        matured_count=matured_count,
        pending_count=pending_count,
        coverage=coverage,
        rank_ic=_spearman(score_values, label_values),
        rmse=math.sqrt(sum(error * error for error in errors) / matured_count),
        mae=sum(abs(error) for error in errors) / matured_count,
        directional_accuracy=sum((score >= 0) == (label >= 0) for score, label in paired)
        / matured_count,
    )


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
