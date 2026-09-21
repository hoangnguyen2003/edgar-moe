import math
from dataclasses import replace
from datetime import date

import numpy as np
import orjson
import pandas as pd
import pytest

from edgar_moe.api.models import EventRecord, SummaryResponse
from edgar_moe.api.repository import SnapshotRepository
from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.modeling.experiment import (
    run_authenticated_experiment,
    save_study_artifacts,
)
from edgar_moe.modeling.frozen import (
    evaluate_frozen_selection,
    save_frozen_evaluation,
)
from edgar_moe.modeling.walk_forward import (
    PROTOCOL_REVISIONS,
    _early_stopping_split,
    run_walk_forward_study,
    save_walk_forward_artifacts,
)
from edgar_moe.reporting import (
    build_authenticated_snapshot,
    build_frozen_snapshot,
    write_frozen_evaluation_report,
    write_research_report,
    write_validation_report,
    write_walk_forward_report,
)
from edgar_moe.settings import ResearchConfig


def study_fixture() -> ResearchDataset:
    generator = np.random.default_rng(19)
    securities = [f"asset-{index:02d}" for index in range(30)]
    rows = []
    for split_index, year in enumerate((2022, 2023, 2024, 2025)):
        for security_index, security_id in enumerate(securities):
            if year < 2025:
                # Filings arrive through the year, as in real data, so each
                # training window has a chronological early-stopping holdout.
                accepted_day = pd.Timestamp(f"{year}-02-01") + pd.Timedelta(days=6 * security_index)
                accepted = f"{accepted_day.date()}T21:30:00Z"
                entry = str((accepted_day + pd.Timedelta(days=1)).date())
                horizon = str((accepted_day + pd.Timedelta(days=29)).date())
            else:
                accepted, entry, horizon = "2025-01-01T21:30:00Z", "2025-01-02", "2025-02-03"
            rows.append(
                {
                    "event_id": f"event-{split_index}-{security_index}",
                    "accession_number": f"0000000001-{21 + split_index * 2:02d}-{security_index:06d}",
                    "cik": f"{security_index + 1:010d}",
                    "security_id": security_id,
                    "ticker": f"T{security_index:02d}",
                    "company_name": f"Test Company {security_index}",
                    "form": "10-Q",
                    "accepted_at": pd.Timestamp(accepted),
                    "report_period": pd.Timestamp(entry).date(),
                    "entry_at": pd.Timestamp(entry, tz="UTC") + pd.Timedelta(hours=14, minutes=30),
                    "entry_date": pd.Timestamp(entry).date(),
                    "horizon_at": pd.Timestamp(horizon, tz="UTC") + pd.Timedelta(hours=21),
                    "exit_date": pd.Timestamp(horizon).date(),
                    "industry_code": str(security_index % 10),
                    "filing_url": "https://www.sec.gov/example",
                    "beta": 0.8 + security_index / 100,
                    "text_missing": False,
                    "fundamental_missing": False,
                    "market_missing": False,
                }
            )
    events = pd.DataFrame(rows)
    event_count = len(events)
    text = generator.normal(size=(event_count, 8)).astype(np.float32)
    fundamental = generator.normal(size=(event_count, 6)).astype(np.float32)
    market = generator.normal(size=(event_count, 4)).astype(np.float32)
    regime = generator.normal(size=(event_count, 3)).astype(np.float32)
    target = (
        0.025 * text[:, 0]
        + 0.015 * fundamental[:, 0]
        + 0.01 * market[:, 0]
        + generator.normal(0, 0.002, event_count)
    ).astype(np.float32)
    test_target = target[-len(securities) :]
    daily_rows = []
    for day_index, day in enumerate(pd.bdate_range("2025-01-02", "2025-02-03")):
        for security_index, security_id in enumerate(securities):
            daily_return = float(test_target[security_index] / 22)
            daily_rows.append(
                {
                    "date": day,
                    "security_id": security_id,
                    "symbol": f"T{security_index:02d}",
                    "return": daily_return,
                    "overnight_return": daily_return * 0.35 if day_index else 0.0,
                    "intraday_return": daily_return * 0.65,
                }
            )
    availability = pd.DataFrame(
        {
            "event_id": events["event_id"],
            "feature_name": "fixture",
            "available_at": pd.to_datetime(events["accepted_at"], utc=True),
            "prediction_at": pd.to_datetime(events["accepted_at"], utc=True),
            "source": "fixture",
        }
    )
    return ResearchDataset(
        dataset_id="research-fixture",
        as_of=date(2025, 12, 31),
        events=events,
        modalities={"text": text, "fundamental": fundamental, "market": market},
        regime=regime,
        target=target,
        daily_returns=pd.DataFrame(daily_rows),
        availability=availability,
        feature_names={
            "text": [f"text_{index}" for index in range(8)],
            "fundamental": [f"fundamental_{index}" for index in range(6)],
            "market": [f"market_{index}" for index in range(4)],
            "regime": [f"regime_{index}" for index in range(3)],
        },
        attrition={"filing_records": event_count, "included_events": event_count},
        source_manifest_hash="a" * 64,
    )


def test_locked_study_exports_artifacts_snapshot_and_report(tmp_path) -> None:
    dataset = study_fixture()
    config = ResearchConfig()
    config.model.candidate_hidden_dims = [8]
    config.model.candidate_dropouts = [0.0]
    config.model.hidden_dim = 8
    config.model.dropout = 0.0
    config.model.expert_dim = 4
    config.model.batch_size = 16
    config.model.max_epochs = 2
    config.model.patience = 2
    config.evaluation.minimum_split_events = 20
    config.evaluation.bootstrap_samples = 20
    config.portfolio.maximum_name_weight = 0.1
    config.portfolio.maximum_industry_exposure = 0.5

    selection = run_authenticated_experiment(
        dataset,
        config=config,
        evaluate_locked_test=False,
        max_epochs=2,
        maximum_candidates=1,
        device="cpu",
    )
    assert np.isnan(selection.scores[selection.split.test]).all()
    assert np.isnan(selection.expert_weights[selection.split.test]).all()
    assert np.isnan(selection.expert_predictions[selection.split.test]).all()
    assert np.isfinite(selection.scores[selection.split.validation]).all()
    assert all(item.test_predictions is None for item in selection.comparisons)
    validation_report = tmp_path / "validation.md"
    write_validation_report(dataset, selection, validation_report, config=config)
    assert "locked test was not evaluated" in validation_report.read_text(encoding="utf-8")

    result = run_authenticated_experiment(
        dataset,
        config=config,
        evaluate_locked_test=True,
        max_epochs=2,
        maximum_candidates=1,
        device="cpu",
    )
    assert result.selected_candidate.name == selection.selected_candidate.name
    assert result.validation_metrics == pytest.approx(selection.validation_metrics)
    artifacts = save_study_artifacts(result, tmp_path / "artifacts", config=config)
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.md"
    build_authenticated_snapshot(dataset, result, snapshot_path, config=config)
    write_research_report(dataset, result, report_path, config=config)
    snapshot = SnapshotRepository(snapshot_path).load()

    SummaryResponse.model_validate(
        {
            "metadata": snapshot["metadata"],
            "summary": snapshot["summary"],
            "predictive_metrics": snapshot["predictive_metrics"],
            "portfolio_scenarios": snapshot["portfolio_scenarios"],
        }
    )
    EventRecord.model_validate(snapshot["events"][0])
    assert result.locked_test_metrics is not None
    assert len(result.comparisons) == 7
    assert (artifacts / "selection.json").exists()
    assert (artifacts / "locked-test.json").exists()
    assert "Authenticated Research Report" in report_path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError, match="Locked test already exists"):
        save_study_artifacts(result, tmp_path / "artifacts", config=config)


def test_walk_forward_selection_never_scores_locked_test(tmp_path) -> None:
    dataset = study_fixture()
    config = ResearchConfig()
    config.model.candidate_hidden_dims = [8]
    config.model.candidate_dropouts = [0.0]
    config.model.candidate_gate_strengths = [0.0]
    config.model.hidden_dim = 8
    config.model.dropout = 0.0
    config.model.gate_strength = 0.0
    config.model.expert_dim = 4
    config.model.batch_size = 16
    config.model.max_epochs = 2
    config.model.patience = 2
    config.evaluation.minimum_split_events = 20
    config.evaluation.bootstrap_samples = 20
    config.portfolio.maximum_name_weight = 0.1
    config.portfolio.maximum_industry_exposure = 0.5

    result = run_walk_forward_study(
        dataset,
        config=config,
        max_epochs=2,
        maximum_candidates=1,
        device="cpu",
    )

    assert [fold.validation_year for fold in result.folds] == [2023, 2024]
    assert [len(fold.train) for fold in result.folds] == [30, 60]
    assert [len(fold.validation) for fold in result.folds] == [30, 30]
    accepted = pd.to_datetime(dataset.events["accepted_at"], utc=True).to_numpy()
    horizon = pd.to_datetime(dataset.events["horizon_at"], utc=True).to_numpy()
    for fold in result.folds:
        inner, holdout = _early_stopping_split(dataset, fold)
        inner_events, holdout_events = fold.train[inner], fold.train[holdout]
        # Early stopping uses the latest training events, never the scored fold,
        # and inner-train labels mature before the holdout begins.
        assert not set(holdout_events) & set(fold.validation)
        assert not set(inner_events) & set(holdout_events)
        assert horizon[inner_events].max() < accepted[holdout_events].min()
        assert len(holdout_events) == math.ceil(0.15 * len(fold.train))
    assert len(result.models) == 9
    assert not result.locked_test_evaluated
    assert len(result.locked_indices) == 30
    assert np.isfinite(result.champion.oof_predictions[result.oof_indices]).all()
    weighted_rank_ic = np.average(
        [result.champion.fold_metrics[fold.name]["rank_ic"] for fold in result.folds],
        weights=[len(fold.validation) for fold in result.folds],
    )
    assert result.champion.aggregate_metrics["rank_ic"] == pytest.approx(weighted_rank_ic)
    assert all(
        np.isnan(model.oof_predictions[result.locked_indices]).all() for model in result.models
    )
    fundamental = next(model for model in result.models if model.name == "Fundamental-Only Expert")
    moe = next(model for model in result.models if model.name.startswith("Shrinkage-Gated"))
    anchored = next(
        model for model in result.models if model.name.startswith("Fundamental-Anchored")
    )
    assert np.allclose(
        anchored.oof_predictions[result.oof_indices],
        (
            0.75 * fundamental.oof_predictions[result.oof_indices]
            + 0.25 * moe.oof_predictions[result.oof_indices]
        ),
    )

    artifact_directory = save_walk_forward_artifacts(
        result,
        tmp_path / "walk-forward",
        config=config,
    )
    report = tmp_path / "walk-forward.md"
    write_walk_forward_report(dataset, result, report, config=config)
    payload = orjson.loads((artifact_directory / "walk-forward-selection.json").read_bytes())

    assert payload["locked_test_evaluated"] is False
    assert payload["locked_test_prediction_count"] == 0
    assert payload["protocol_revisions"] == PROTOCOL_REVISIONS
    assert payload["champion"]["name"] == result.champion.name
    assert (artifact_directory / "oof-predictions.npz").exists()
    assert "locked test was not transformed" in report.read_text(encoding="utf-8")

    selection_path = artifact_directory / "walk-forward-selection.json"
    with pytest.raises(ValueError, match="Confirmation hash"):
        evaluate_frozen_selection(
            dataset,
            selection_path,
            confirmation_hash="0" * 64,
            device="cpu",
        )

    frozen = evaluate_frozen_selection(
        dataset,
        selection_path,
        confirmation_hash=str(payload["selection_hash"]),
        device="cpu",
    )
    recovery_note = "First attempt stopped before persistence because of a timezone mismatch."
    frozen_directory = save_frozen_evaluation(
        frozen,
        tmp_path / "frozen",
        recovery_note=recovery_note,
    )
    frozen_payload = orjson.loads((frozen_directory / "locked-test.json").read_bytes())
    frozen_report = tmp_path / "frozen.md"
    write_frozen_evaluation_report(
        dataset,
        frozen,
        frozen_report,
        recovery_note=recovery_note,
        locked_test_hash=str(frozen_payload["locked_test_hash"]),
    )

    assert len(frozen.split.train) == 90
    assert len(frozen.split.test) == 30
    assert np.isfinite(frozen.scores[frozen.split.test]).all()
    assert np.isnan(frozen.scores[frozen.split.train]).all()
    assert np.isfinite(frozen.test_metrics["rank_ic"])
    assert (frozen_directory / "locked-test.json").exists()
    assert (frozen_directory / "frozen-model.pt").exists()
    assert (frozen_directory / "locked-test-scores.npz").exists()
    assert not list((tmp_path / "frozen").glob(".*.staging-*"))
    assert frozen_payload["opening_audit"] == {
        "completed_attempt": 2,
        "recovery_note": recovery_note,
    }
    report_text = frozen_report.read_text(encoding="utf-8")
    assert "Frozen Locked-Test Report" in report_text
    assert recovery_note in report_text
    assert str(frozen_payload["locked_test_hash"]) in report_text
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        save_frozen_evaluation(frozen, tmp_path / "frozen")

    partial_root = tmp_path / "partial-frozen"
    partial_directory = partial_root / frozen.dataset_id
    partial_directory.mkdir(parents=True)
    partial_model = partial_directory / "frozen-model.pt"
    partial_model.write_bytes(b"partial artifact")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        save_frozen_evaluation(frozen, partial_root)
    assert partial_model.read_bytes() == b"partial artifact"

    anchored_selection = replace(
        result,
        run_id=f"{result.run_id}-anchored-fixture",
        champion=anchored,
    )
    anchored_directory = save_walk_forward_artifacts(
        anchored_selection,
        tmp_path / "anchored-walk-forward",
        config=config,
    )
    anchored_payload = orjson.loads(
        (anchored_directory / "walk-forward-selection.json").read_bytes()
    )
    anchored_frozen = evaluate_frozen_selection(
        dataset,
        anchored_directory / "walk-forward-selection.json",
        confirmation_hash=str(anchored_payload["selection_hash"]),
        device="cpu",
    )
    frozen_snapshot_path = tmp_path / "frozen-snapshot.json"
    build_frozen_snapshot(
        dataset,
        anchored_frozen,
        frozen_snapshot_path,
        locked_test_hash="f" * 64,
        recovery_note=recovery_note,
    )
    frozen_snapshot = SnapshotRepository(frozen_snapshot_path).load()
    SummaryResponse.model_validate(
        {
            "metadata": frozen_snapshot["metadata"],
            "summary": frozen_snapshot["summary"],
            "predictive_metrics": frozen_snapshot["predictive_metrics"],
            "portfolio_scenarios": frozen_snapshot["portfolio_scenarios"],
        }
    )
    EventRecord.model_validate(frozen_snapshot["events"][0])
    assert frozen_snapshot["metadata"]["selection_hash"] == anchored_frozen.selection_hash
    assert frozen_snapshot["metadata"]["locked_test_hash"] == "f" * 64
    assert frozen_snapshot["metadata"]["opening_attempt"] == 2
