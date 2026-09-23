"""Read-only baseline audit: python -m edgar_moe.forward.audit diagnostic.json."""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from edgar_moe.forward.diagnostics import _first_event_forecasts
from edgar_moe.forward.metrics import forward_metrics
from edgar_moe.forward.uncertainty import clustered_rank_ic_interval


def _descriptive_metrics(
    scores: list[float], labels: list[float], *, forecast_count: int
) -> dict[str, Any]:
    """Keep descriptive metrics without the helper's independent-pair interval."""
    result = asdict(forward_metrics(scores, labels, forecast_count=forecast_count))
    result["rank_ic_low"] = None
    result["rank_ic_high"] = None
    return result


def _official_metrics(rows: Sequence[Mapping[str, Any]], *, forecast_count: int) -> dict[str, Any]:
    scores = [float(row["score"]) for row in rows]
    labels = [float(row["realized_abnormal_return"]) for row in rows]
    accepted_at: list[datetime] = []
    for row in rows:
        try:
            timestamp = datetime.fromisoformat(str(row.get("accepted_at", "")))
        except ValueError as exc:
            raise ValueError("Aware accepted_at is required for settled forecasts") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Aware accepted_at is required for settled forecasts")
        accepted_at.append(timestamp)
    result = _descriptive_metrics(scores, labels, forecast_count=forecast_count)
    interval = clustered_rank_ic_interval(scores, labels, accepted_at)
    result.update(
        rank_ic_low=interval.low,
        rank_ic_high=interval.high,
        rank_ic_interval_method="calendar_month_moving_block",
        rank_ic_interval_status=interval.status,
        rank_ic_calendar_months=interval.calendar_months,
        rank_ic_block_months=interval.block_months,
        rank_ic_bootstrap_samples=interval.resamples,
    )
    return result


def audit_official(page: dict[str, Any]) -> dict[str, Any]:
    """Audit a complete forecast export without recomputing official labels."""
    rows = page.get("items", [])
    if page.get("offset") != 0 or page.get("total") != len(rows):
        raise ValueError("A complete forecast export starting at offset zero is required")
    if len({row.get("forecast_id") for row in rows}) != len(rows):
        raise ValueError("Duplicate forecast IDs")
    if len({row.get("model_id") for row in rows}) > 1:
        raise ValueError("Audit one model at a time")
    selected = _first_event_forecasts(rows)
    if selected is None:
        raise ValueError("Valid identities and aware forecast timestamps are required")
    # Select before looking at labels. A labeled repeat cannot replace an
    # unlabeled first forecast.
    matured = [r for r in selected if r.get("realized_abnormal_return") is not None]
    scores = [float(r["score"]) for r in matured]
    labels = [float(r["realized_abnormal_return"]) for r in matured]
    if not all(math.isfinite(v) for v in scores + labels):
        raise ValueError("Non-finite scores or outcomes")
    paired = [r for r in matured if r.get("fundamental_score") is not None]
    anchor = [float(r["fundamental_score"]) for r in paired]
    if not all(math.isfinite(v) for v in anchor):
        raise ValueError("Non-finite component scores")
    model = _official_metrics(matured, forecast_count=len(selected))
    zero = forward_metrics([0.0] * len(labels), labels, forecast_count=len(selected))
    return {
        "evaluation": "read_only_audit_of_recorded_official_labels",
        "selection_rule": "earliest_forecast_per_model_and_event",
        "model_id": rows[0]["model_id"] if rows else None,
        "raw_forecast_count": len(rows),
        "unique_event_count": len(selected),
        "repeated_forecast_count": len(rows) - len(selected),
        "selected_forecast_ids": [r["forecast_id"] for r in selected],
        "model": model,
        "zero_return_baseline": {"rmse": zero.rmse, "mae": zero.mae},
        "negative_predictions": sum(v < 0 for v in scores),
        "negative_outcomes": sum(v < 0 for v in labels),
        "always_negative_accuracy": sum(v < 0 for v in labels) / len(labels) if labels else None,
        "always_nonnegative_accuracy": sum(v >= 0 for v in labels) / len(labels)
        if labels
        else None,
        "component_comparison": {
            "paired_count": len(paired),
            "missing_component_count": len(matured) - len(paired),
            "model": _official_metrics(paired, forecast_count=len(paired)),
            "fundamental_anchor": _official_metrics(
                [{**row, "score": row["fundamental_score"]} for row in paired],
                forecast_count=len(paired),
            ),
        },
        "limitations": (
            "Exploratory audit, not a replacement for published registry metrics. "
            "Unlabeled events may be pending or awaiting settlement. "
            "Clustered rank-IC intervals require at least 100 settled events in 12 "
            "acceptance months; unavailable bounds are not evidence of no effect. "
            "No model refitting or independent significance claim."
        ),
    }


def audit_diagnostic(report: dict[str, Any]) -> dict[str, Any]:
    """Compare fixed baselines on exactly the same finite, unique observations."""
    if report.get("diagnostic") is not True:
        raise ValueError("A diagnostic-only report is required")
    unique = report.get("unique_event_evaluation", {})
    if unique.get("selection_rule") != "earliest_forecast_per_model_and_event":
        raise ValueError("An earliest-per-model/event diagnostic is required")
    rows = unique.get("observations", [])
    identities = [(row["model_id"], row["event_id"]) for row in rows]
    if len(set(identities)) != len(rows):
        raise ValueError("Duplicate model/event observations")
    scores = [float(row["score"]) for row in rows]
    labels = [float(row["realized_abnormal_return"]) for row in rows]
    if not all(math.isfinite(value) for value in scores + labels):
        raise ValueError("Non-finite scores or outcomes")
    count = len(rows)
    if count != unique.get("matured_count"):
        raise ValueError("Observation count disagrees with matured_count")
    zero = forward_metrics([0.0] * count, labels, forecast_count=count)
    sensitivity = [
        forward_metrics(
            scores[:index] + scores[index + 1 :],
            labels[:index] + labels[index + 1 :],
            forecast_count=count - 1,
        ).rank_ic
        for index in range(count)
    ]
    finite_ic = [value for value in sensitivity if value is not None]
    return {
        "as_of": report.get("as_of"),
        "horizon_sessions": report.get("horizon_sessions"),
        "evaluated_unique_events": count,
        "sample_scope": "Matured observations only; coverage below is within this sample",
        "negative_predictions": sum(value < 0 for value in scores),
        "positive_predictions": sum(value > 0 for value in scores),
        "zero_predictions": sum(value == 0 for value in scores),
        "negative_outcomes": sum(value < 0 for value in labels),
        "positive_outcomes": sum(value > 0 for value in labels),
        "zero_outcomes": sum(value == 0 for value in labels),
        "model": {
            **_descriptive_metrics(scores, labels, forecast_count=count),
            "rank_ic_interval_method": "not_estimated",
            "rank_ic_interval_status": "short_horizon_diagnostic",
        },
        "zero_return_baseline": {"rmse": zero.rmse, "mae": zero.mae},
        # Match the production metric's non-negative versus negative convention.
        "always_nonnegative_accuracy": sum(value >= 0 for value in labels) / count
        if count
        else None,
        "always_negative_accuracy": sum(value < 0 for value in labels) / count if count else None,
        "leave_one_out_rank_ic_min": min(finite_ic, default=None),
        "leave_one_out_rank_ic_max": max(finite_ic, default=None),
        "limitations": (
            "Exploratory short-horizon audit, not official model validation. "
            "No rank-IC confidence interval is estimated; leave-one-out range is "
            "not a confidence interval. "
            "Observations may share market exposures and overlapping horizons."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diagnostic", type=Path)
    parser.add_argument(
        "--official-export",
        action="store_true",
        help="Read a complete forecast API export with recorded labels",
    )
    args = parser.parse_args()
    report = json.loads(args.diagnostic.read_text())
    audit = audit_official(report) if args.official_export else audit_diagnostic(report)
    print(json.dumps(audit, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
