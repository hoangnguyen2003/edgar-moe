from __future__ import annotations

import sys
from pathlib import Path

from edgar_moe.api.models import (
    EventRecord,
    ExperimentRecord,
    FreshnessResponse,
    MethodologyResponse,
    SummaryResponse,
)
from edgar_moe.api.repository import SnapshotRepository


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/demo/snapshot.json")
    snapshot = SnapshotRepository(path).load()
    summary = SummaryResponse.model_validate(
        {
            "metadata": snapshot["metadata"],
            "summary": snapshot["summary"],
            "predictive_metrics": snapshot["predictive_metrics"],
            "portfolio_scenarios": snapshot["portfolio_scenarios"],
        }
    )
    for row in snapshot["experiments"]:
        ExperimentRecord.model_validate(row)
    for row in [*snapshot["events"], *snapshot["latest_signals"]]:
        event = EventRecord.model_validate(row)
        if event.accepted_at.date() > summary.metadata.as_of:
            raise ValueError(
                f"Event {event.accession_number} was accepted after snapshot as-of date"
            )
        if (
            event.horizon_date > summary.metadata.as_of
            and event.realized_abnormal_return is not None
        ):
            raise ValueError(
                f"Event {event.accession_number} exposes an unmatured forward label"
            )
    MethodologyResponse.model_validate(snapshot["methodology"])
    FreshnessResponse.model_validate(snapshot["freshness"])
    print(f"Validated {path} ({len(snapshot['events'])} public events)")


if __name__ == "__main__":
    main()
