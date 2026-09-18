"""Read-only baseline audit: python -m edgar_moe.forward.audit diagnostic.json."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from edgar_moe.forward.metrics import forward_metrics


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
    model = forward_metrics(scores, labels, forecast_count=count)
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
        "model": asdict(model),
        "zero_return_baseline": {"rmse": zero.rmse, "mae": zero.mae},
        # Match the production metric's non-negative versus negative convention.
        "always_nonnegative_accuracy": sum(value >= 0 for value in labels) / count
        if count else None,
        "always_negative_accuracy": sum(value < 0 for value in labels) / count
        if count else None,
        "leave_one_out_rank_ic_min": min(finite_ic, default=None),
        "leave_one_out_rank_ic_max": max(finite_ic, default=None),
        "limitations": (
            "Exploratory short-horizon audit, not official model validation. "
            "Leave-one-out range is not a confidence interval. "
            "Observations may share market exposures and overlapping horizons."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diagnostic", type=Path)
    args = parser.parse_args()
    report = json.loads(args.diagnostic.read_text())
    print(json.dumps(audit_diagnostic(report), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
