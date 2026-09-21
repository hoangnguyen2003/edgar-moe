from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import torch

from edgar_moe.data.storage import sha256_file
from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.forward.inference import ForecastQualityError, FrozenPredictor
from edgar_moe.modeling.moe import RegimeGatedMoE


def test_hash_pinned_frozen_predictor_scores_only_pre_entry_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = RegimeGatedMoE(
        text_dim=2,
        fundamental_dim=2,
        market_dim=2,
        regime_dim=1,
        hidden_dim=4,
        expert_dim=3,
        dropout=0.0,
        gate_strength=0.5,
    )
    artifact = tmp_path / "frozen-model.pt"
    torch.save(
        {
            "selection_hash": "a" * 64,
            "champion_family": "anchored_multimodal",
            "champion_parameters": {
                "fundamental_anchor_weight": 0.75,
                "moe_residual_weight": 0.25,
            },
            "target_mean": 0.01,
            "target_std": 0.1,
            "moe_state_dict": model.state_dict(),
            "moe_config": model.export_config(),
            "preprocessor": {
                name: {
                    "medians": np.zeros(2),
                    "mean": np.zeros(2),
                    "scale": np.ones(2),
                }
                for name in ("text", "fundamental", "market")
            },
            "regime": {
                "medians": np.zeros(1),
                "mean": np.zeros(1),
                "scale": np.ones(1),
            },
            "fundamental_elastic_net": {
                "coef": np.array([0.1, -0.2]),
                "intercept": 0.03,
            },
        },
        artifact,
    )
    digest = sha256_file(artifact)
    predictor = FrozenPredictor.load(
        artifact,
        expected_sha256=digest,
        expected_selection_hash="a" * 64,
    )
    forecast_as_of = datetime(2026, 8, 7, 1, 0, tzinfo=UTC)
    events = pd.DataFrame(
        [
            event("event-1", "AAA", forecast_as_of, entry_offset_hours=12),
            event("event-2", "BBB", forecast_as_of, entry_offset_hours=14),
            event("event-too-late", "OLD", forecast_as_of, entry_offset_hours=-1),
        ]
    )
    dataset = ResearchDataset(
        dataset_id="fixture",
        as_of=date(2026, 8, 7),
        events=events,
        modalities={
            "text": np.array([[0.1, 0.2], [0.3, 0.1], [0.0, 0.0]], dtype=np.float32),
            "fundamental": np.array([[0.2, -0.1], [-0.3, 0.4], [0.0, 0.0]], dtype=np.float32),
            "market": np.array([[0.4, 0.2], [0.1, -0.2], [0.0, 0.0]], dtype=np.float32),
        },
        regime=np.array([[0.2], [-0.1], [0.0]], dtype=np.float32),
        target=np.array([99.0, 99.0, 99.0]),
        daily_returns=pd.DataFrame(),
        availability=pd.DataFrame(
            {
                "event_id": ["event-1", "event-2"],
                "feature_name": ["fixture", "fixture"],
                "available_at": [forecast_as_of - timedelta(hours=1)] * 2,
                "prediction_at": [forecast_as_of] * 2,
            }
        ),
        feature_names={},
        attrition={},
        source_manifest_hash="b" * 64,
    )

    batch = predictor.forecast(dataset, as_of=forecast_as_of)

    assert [item.ticker for item in batch.forecasts] == ["AAA", "BBB"]
    assert sorted(item.rank for item in batch.forecasts) == [0.5, 1.0]
    assert all(sum(item.expert_weights.values()) == pytest.approx(1.0) for item in batch.forecasts)
    assert all(item.fundamental_score is not None for item in batch.forecasts)
    assert batch.checks[0].status == "passed"

    drift_outputs = predictor.component_outputs(dataset)
    dataset.target[:] = np.array([999.0, -999.0, 123.0])
    changed_target_outputs = predictor.component_outputs(dataset)
    assert {"moe_score", "final_score", "expert_weight:text"}.issubset(drift_outputs)
    assert all(len(values) == len(dataset.events) for values in drift_outputs.values())
    for name in drift_outputs:
        np.testing.assert_array_equal(drift_outputs[name], changed_target_outputs[name])

    # Known standardized neural outputs isolate normalization from the network.
    # The fundamental expert already predicts raw returns and must not be
    # de-standardized a second time.
    monkeypatch.setattr(
        "edgar_moe.forward.inference.predict_moe",
        lambda *args, **kwargs: SimpleNamespace(
            scores=np.array([2.0, -1.0]),
            expert_predictions=np.array([[2.0, 2.0, 2.0], [-1.0, -1.0, -1.0]]),
            expert_weights=np.full((2, 3), 1 / 3),
        ),
    )
    known_batch = predictor.forecast(dataset, as_of=forecast_as_of)
    expected_anchor = [0.07, -0.08]
    expected_neural = [0.21, -0.09]
    for row, anchor, neural in zip(
        known_batch.forecasts,
        expected_anchor,
        expected_neural,
        strict=True,
    ):
        assert row.fundamental_score == pytest.approx(anchor)
        assert row.score == pytest.approx(0.75 * anchor + 0.25 * neural)
        assert all(value == pytest.approx(neural) for value in row.expert_predictions.values())

    with pytest.raises(ValueError, match="unexpected SHA-256"):
        FrozenPredictor.load(
            artifact,
            expected_sha256="f" * 64,
            expected_selection_hash="a" * 64,
        )


def event(
    event_id: str,
    ticker: str,
    forecast_as_of: datetime,
    *,
    entry_offset_hours: int,
) -> dict[str, object]:
    entry_at = forecast_as_of + timedelta(hours=entry_offset_hours)
    return {
        "event_id": event_id,
        "accession_number": f"0000000000-26-{len(event_id):0>6}",
        "security_id": f"asset-{ticker}",
        "ticker": ticker,
        "company_name": f"{ticker} Corp",
        "form": "10-Q",
        "accepted_at": forecast_as_of - timedelta(hours=1),
        "entry_at": entry_at,
        "entry_date": entry_at.date(),
        "horizon_at": forecast_as_of + timedelta(days=30),
        "industry_code": "3571",
    }


def _write_artifact(path: Path, **extra: object) -> str:
    model = RegimeGatedMoE(
        text_dim=2,
        fundamental_dim=2,
        market_dim=2,
        regime_dim=1,
        hidden_dim=4,
        expert_dim=3,
        dropout=0.0,
        gate_strength=0.5,
    )
    torch.save(
        {
            "selection_hash": "a" * 64,
            "champion_family": "multimodal",
            "champion_parameters": {},
            "target_mean": 0.0,
            "target_std": 1.0,
            "moe_state_dict": model.state_dict(),
            "moe_config": model.export_config(),
            "preprocessor": {
                name: {"medians": np.zeros(2), "mean": np.zeros(2), "scale": np.ones(2)}
                for name in ("text", "fundamental", "market")
            },
            "regime": {"medians": np.zeros(1), "mean": np.zeros(1), "scale": np.ones(1)},
            **extra,
        },
        path,
    )
    return sha256_file(path)


def _one_event_dataset(
    forecast_as_of: datetime,
    *,
    provenance: dict[str, str] | None = None,
    feature_available_at: datetime | None = None,
) -> ResearchDataset:
    return ResearchDataset(
        dataset_id="fixture",
        as_of=date(2026, 8, 7),
        events=pd.DataFrame([event("event-1", "AAA", forecast_as_of, entry_offset_hours=12)]),
        modalities={
            name: np.array([[0.1, 0.2]], dtype=np.float32)
            for name in ("text", "fundamental", "market")
        },
        regime=np.array([[0.2]], dtype=np.float32),
        target=np.array([np.nan]),
        daily_returns=pd.DataFrame(),
        availability=pd.DataFrame(
            {
                "event_id": ["event-1"],
                "feature_name": ["fixture"],
                "available_at": [feature_available_at or forecast_as_of - timedelta(hours=1)],
                "prediction_at": [forecast_as_of - timedelta(minutes=30)],
            }
        ),
        feature_names={},
        attrition={},
        source_manifest_hash="b" * 64,
        provenance=provenance or {},
    )


def test_frozen_predictor_refuses_features_built_under_another_fact_policy(
    tmp_path: Path,
) -> None:
    forecast_as_of = datetime(2026, 8, 7, 1, 0, tzinfo=UTC)
    legacy_artifact = tmp_path / "legacy.pt"
    legacy = FrozenPredictor.load(
        legacy_artifact,
        expected_sha256=_write_artifact(legacy_artifact),
        expected_selection_hash="a" * 64,
    )
    v2_dataset = _one_event_dataset(
        forecast_as_of, provenance={"xbrl_fact_policy": "duration_aware_v2"}
    )

    # Artifacts without the field (frozen v1) were trained on legacy features.
    assert legacy.xbrl_fact_policy == "legacy_v1"
    with pytest.raises(ValueError, match="XBRL fact policy"):
        legacy.forecast(v2_dataset, as_of=forecast_as_of)
    with pytest.raises(ValueError, match="XBRL fact policy"):
        legacy.component_outputs(v2_dataset)
    assert (
        len(legacy.forecast(_one_event_dataset(forecast_as_of), as_of=forecast_as_of).forecasts)
        == 1
    )

    v2_artifact = tmp_path / "v2.pt"
    v2 = FrozenPredictor.load(
        v2_artifact,
        expected_sha256=_write_artifact(v2_artifact, xbrl_fact_policy="duration_aware_v2"),
        expected_selection_hash="a" * 64,
    )
    assert len(v2.forecast(v2_dataset, as_of=forecast_as_of).forecasts) == 1
    with pytest.raises(ValueError, match="XBRL fact policy"):
        v2.forecast(_one_event_dataset(forecast_as_of), as_of=forecast_as_of)


def test_point_in_time_violation_carries_its_failed_quality_check(tmp_path: Path) -> None:
    forecast_as_of = datetime(2026, 8, 7, 1, 0, tzinfo=UTC)
    artifact = tmp_path / "frozen-model.pt"
    predictor = FrozenPredictor.load(
        artifact,
        expected_sha256=_write_artifact(artifact),
        expected_selection_hash="a" * 64,
    )
    leaked = _one_event_dataset(forecast_as_of, feature_available_at=forecast_as_of)

    with pytest.raises(
        ForecastQualityError, match="Point-in-time availability audit failed"
    ) as error:
        predictor.forecast(leaked, as_of=forecast_as_of)

    availability_check = error.value.checks[0]
    assert availability_check.name == "point_in_time_availability"
    assert availability_check.status == "failed"
    assert availability_check.observed_value == 1.0
