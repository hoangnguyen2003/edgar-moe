from datetime import date

import numpy as np
import pandas as pd
import pytest

from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.features.drift import (
    DriftThresholds,
    build_research_drift_report,
    verify_research_drift_report,
)


def make_dataset(
    dataset_id: str,
    as_of: date,
    *,
    text: np.ndarray,
    market: np.ndarray | None = None,
) -> ResearchDataset:
    row_count = len(text)
    fundamental = np.column_stack(
        [np.linspace(0.1, 0.2, row_count), np.linspace(-0.1, 0.1, row_count)]
    ).astype(np.float32)
    market_values = (
        market
        if market is not None
        else np.column_stack([np.ones(row_count), np.zeros(row_count)]).astype(np.float32)
    )
    events = pd.DataFrame({"event_id": [f"event-{index}" for index in range(row_count)]})
    availability = pd.DataFrame(
        {
            "event_id": events["event_id"],
            "feature_name": "fixture",
            "available_at": pd.Timestamp("2025-01-01", tz="UTC"),
            "prediction_at": pd.Timestamp("2025-01-01", tz="UTC"),
        }
    )
    return ResearchDataset(
        dataset_id=dataset_id,
        as_of=as_of,
        events=events,
        modalities={
            "text": np.asarray(text, dtype=np.float32),
            "fundamental": fundamental,
            "market": np.asarray(market_values, dtype=np.float32),
        },
        regime=np.column_stack(
            [np.linspace(-1.0, 1.0, row_count), np.zeros(row_count)]
        ).astype(np.float32),
        target=np.full(row_count, np.nan, dtype=np.float32),
        daily_returns=pd.DataFrame(),
        availability=availability,
        feature_names={
            "text": ["text_a", "text_b"],
            "fundamental": ["fundamental_a", "fundamental_b"],
            "market": ["market_a", "market_b"],
            "regime": ["regime_a", "regime_b"],
        },
        attrition={"included_events": row_count},
        source_manifest_hash=dataset_id + "-source",
    )


def test_drift_report_measures_missingness_and_component_outputs() -> None:
    baseline = make_dataset(
        "training",
        date(2025, 1, 1),
        text=np.array([[0.0, 1.0], [0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]),
    )
    prospective = make_dataset(
        "prospective",
        date(2025, 2, 1),
        text=np.array([[10.0, np.nan], [10.0, np.nan], [10.0, np.nan], [10.0, np.nan]]),
    )

    report = build_research_drift_report(
        baseline,
        prospective,
        baseline_components={"final_score": np.zeros(4), "expert_weights": np.full((4, 3), 1 / 3)},
        prospective_components={
            "final_score": np.full(4, 2.0),
            "expert_weights": np.full((4, 3), 1 / 3),
        },
    )

    assert report["scope"] == "research_drift_only"
    assert report["v1_immutable"] is True
    assert report["automatic_retraining"] is False
    assert report["status"] == "warning"
    assert report["features"]["text"]["features"][1]["status"] == "warning"
    final_score = next(
        row for row in report["component_outputs"]["components"] if row["name"] == "final_score"
    )
    assert final_score["status"] == "warning"
    assert len(report["report_hash"]) == 64
    assert report["summary"]["component_count"] == 4
    verify_research_drift_report(report)
    report["status"] = "stable"
    with pytest.raises(ValueError, match="content hash mismatch"):
        verify_research_drift_report(report)


def test_drift_report_is_incomplete_without_model_components() -> None:
    baseline = make_dataset(
        "training",
        date(2025, 1, 1),
        text=np.ones((3, 2)),
    )
    prospective = make_dataset(
        "prospective",
        date(2025, 1, 2),
        text=np.ones((3, 2)),
    )

    report = build_research_drift_report(baseline, prospective)

    assert report["status"] == "incomplete"
    assert report["component_outputs"]["status"] == "not_supplied"
    assert report["features"]["text"]["status"] == "stable"


def test_drift_report_rejects_datasets_built_with_different_fact_policies() -> None:
    baseline = make_dataset("training", date(2025, 2, 1), text=np.ones((2, 2)))
    prospective = make_dataset("prospective", date(2025, 3, 1), text=np.ones((2, 2)))
    prospective.provenance["xbrl_fact_policy"] = "duration_aware_v2"

    # The frozen v1 training dataset predates the setting and is legacy_v1, so
    # comparing it with duration-aware features would report spurious drift.
    with pytest.raises(ValueError, match="one XBRL fact policy"):
        build_research_drift_report(baseline, prospective)


def test_drift_report_rejects_dimension_and_temporal_mismatch() -> None:
    baseline = make_dataset(
        "training",
        date(2025, 2, 1),
        text=np.ones((2, 2)),
    )
    prospective = make_dataset(
        "prospective",
        date(2025, 1, 1),
        text=np.ones((2, 2)),
        market=np.ones((2, 3)),
    )
    with pytest.raises(ValueError, match="must not precede"):
        build_research_drift_report(baseline, prospective)

    later = make_dataset(
        "prospective",
        date(2025, 3, 1),
        text=np.ones((2, 2)),
        market=np.ones((2, 3)),
    )
    report = build_research_drift_report(
        baseline,
        later,
        baseline_components={"score": np.ones(2)},
        prospective_components={"score": np.ones(2)},
        thresholds=DriftThresholds(),
    )
    assert report["status"] == "error"
    assert report["summary"]["dimension_mismatch_modalities"] == ["market"]
    assert report["features"]["market"]["dimension_match"] is False
