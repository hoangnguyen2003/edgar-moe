from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import orjson
import pandas as pd
import pytest
from typer.testing import CliRunner

from edgar_moe.backtest.metrics import predictive_metrics
from edgar_moe.cli import app
from edgar_moe.data.storage import sha256_file
from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.modeling.pretest_review import (
    SIMPLE_COMPARATORS,
    _portfolio_cost_review,
    review_v2_pretest,
)
from edgar_moe.modeling.walk_forward import PROTOCOL_REVISIONS, make_walk_forward_folds
from edgar_moe.settings import ResearchConfig


def _fixture(tmp_path: Path) -> tuple[ResearchDataset, Path]:
    rng = np.random.default_rng(41)
    rows = []
    for year in (2022, 2023, 2024, 2025):
        for month in range(1, 13):
            for security in range(3):
                accepted = pd.Timestamp(year=year, month=month, day=10 + security, tz="UTC")
                rows.append(
                    {
                        "accepted_at": accepted,
                        "horizon_at": accepted + pd.Timedelta(days=20),
                        "entry_date": (accepted + pd.Timedelta(days=1)).date(),
                        "exit_date": (accepted + pd.Timedelta(days=20)).date(),
                        "security_id": f"security-{security}",
                        "ticker": f"T{security}",
                        "beta": 1.0,
                        "industry_code": "fixture",
                    }
                )
    events = pd.DataFrame(rows)
    target = rng.normal(0, 0.05, len(events)).astype(np.float32)
    target[len(events) - 36 :] = np.nan  # A poisoned locked period must never enter the review.
    dataset = ResearchDataset(
        dataset_id="research-duration-aware-v2-fixture",
        as_of=date(2025, 12, 31),
        events=events,
        modalities={
            name: np.zeros((len(events), 1), dtype=np.float32)
            for name in ("text", "fundamental", "market")
        },
        regime=np.zeros((len(events), 1), dtype=np.float32),
        target=target,
        daily_returns=pd.DataFrame(
            {"date": [pd.Timestamp("2024-01-01")], "security_id": ["security-0"], "return": [0.0]}
        ),
        availability=pd.DataFrame(
            {"event_id": ["fixture"], "available_at": [pd.Timestamp("2022-01-01", tz="UTC")]}
        ),
        feature_names={name: [name] for name in ("text", "fundamental", "market", "regime")},
        attrition={"included_events": len(events)},
        source_manifest_hash="a" * 64,
        provenance={"xbrl_fact_policy": "duration_aware_v2"},
    )
    config = ResearchConfig()
    config.features.xbrl_fact_policy = "duration_aware_v2"
    config.evaluation.minimum_split_events = 10
    folds = make_walk_forward_folds(dataset, config)
    indices = np.concatenate([fold.validation for fold in folds])
    names = ["Shrinkage-Gated MoE fixture", *SIMPLE_COMPARATORS]
    scores = np.full((len(events), len(names)), np.nan, dtype=np.float64)
    for column, _name in enumerate(names):
        noise = 0.02 if column == 0 else 0.04 + column * 0.005
        scores[indices, column] = target[indices] + rng.normal(0, noise, len(indices))
    selection_dir = tmp_path / "selection"
    selection_dir.mkdir()
    archive_path = selection_dir / "oof-predictions.npz"
    np.savez_compressed(
        archive_path,
        model_names=np.asarray(names, dtype=np.str_),
        predictions=scores,
        oof_indices=indices,
    )
    models = []
    for column, name in enumerate(names):
        fold_metrics = {
            fold.name: predictive_metrics(target[fold.validation], scores[fold.validation, column])
            for fold in folds
        }
        pooled = predictive_metrics(target[indices], scores[indices, column])
        weighted_ic = float(
            np.average(
                [fold_metrics[fold.name]["rank_ic"] for fold in folds],
                weights=[len(fold.validation) for fold in folds],
            )
        )
        models.append(
            {
                "name": name,
                "family": "multimodal" if column == 0 else "linear",
                "parameters": {},
                "selected": column == 0,
                "worst_fold_rank_ic": min(item["rank_ic"] for item in fold_metrics.values()),
                "aggregate_metrics": {**pooled, "rank_ic": weighted_ic},
                "fold_metrics": fold_metrics,
            }
        )
    payload = {
        "run_id": "fixture-v2-run",
        "dataset_id": dataset.dataset_id,
        "configuration": config.model_dump(mode="json"),
        "protocol_revisions": PROTOCOL_REVISIONS,
        "locked_test_start": config.evaluation.test_start,
        "locked_test_evaluated": False,
        "locked_test_prediction_count": 0,
        "oof_predictions_sha256": sha256_file(archive_path),
        "folds": [
            {
                "name": fold.name,
                "validation_year": fold.validation_year,
                "train_events": len(fold.train),
                "validation_events": len(fold.validation),
                "train_label_cutoff": fold.train_label_cutoff,
                "validation_start": fold.validation_start,
                "validation_end": fold.validation_end,
            }
            for fold in folds
        ],
        "champion": models[0],
        "models": models,
    }
    payload["selection_hash"] = hashlib.sha256(
        orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    (selection_dir / "walk-forward-selection.json").write_bytes(
        orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    )
    return dataset, selection_dir


def test_v2_review_compares_paired_pretest_scores_without_opening_locked_period(
    tmp_path: Path,
) -> None:
    dataset, selection_dir = _fixture(tmp_path)

    review = review_v2_pretest(dataset, selection_dir, bootstrap_samples=100)

    assert review["status"] == "pretest_development_only"
    assert review["locked_test_predictions"] == 0
    assert review["xbrl_fact_policy"] == "duration_aware_v2"
    assert review["oof_events"] > 30
    assert len(review["comparisons"]) == len(SIMPLE_COMPARATORS)
    assert all(item["delta_interval_95"]["status"] == "ready" for item in review["comparisons"])
    assert review["uncertainty"]["conditional_on_selection"] is True
    assert review["portfolio"]["status"] == "unavailable_return_calendar"


def test_v2_portfolio_review_requires_complete_returns_and_charges_costs(tmp_path: Path) -> None:
    dataset, _selection_dir = _fixture(tmp_path)
    events = dataset.events.iloc[:2].copy()
    events["entry_date"] = pd.Timestamp("2023-01-02").date()
    events["exit_date"] = pd.Timestamp("2023-02-20").date()
    dates = pd.date_range("2023-01-02", "2023-02-20", freq="B")
    returns = pd.DataFrame(
        [
            {
                "date": day,
                "security_id": security,
                "return": 0.001 if security == "security-0" else -0.001,
            }
            for day in dates
            for security in ("security-0", "security-1")
        ]
    )
    narrowed = replace(dataset, events=events, daily_returns=returns)
    config = ResearchConfig()
    names = ["candidate", "Fundamental-Only Expert"]
    scores = np.asarray([[1.0, 0.5], [-1.0, -0.5]])

    result = _portfolio_cost_review(narrowed, np.asarray([0, 1]), names, scores, names[0], config)

    assert result["status"] == "development_only"
    assert result["observed_trading_days"] == len(dates)
    assert [item["cost_bps"] for item in result["models"][0]["scenarios"]] == [10, 25, 50]
    assert (
        result["models"][0]["scenarios"][0]["annualized_return"]
        >= result["models"][0]["scenarios"][2]["annualized_return"]
    )

    missing = returns.loc[
        ~((returns["date"] == dates[5]) & (returns["security_id"] == "security-1"))
    ]
    narrowed = replace(narrowed, daily_returns=missing)
    rejected = _portfolio_cost_review(narrowed, np.asarray([0, 1]), names, scores, names[0], config)
    assert rejected["status"] == "unavailable_incomplete_return_coverage"
    assert rejected["missing_active_security_days"] == 1


def test_v2_review_rejects_legacy_policy_and_tampered_scores(tmp_path: Path) -> None:
    dataset, selection_dir = _fixture(tmp_path)
    with pytest.raises(ValueError, match="duration_aware_v2"):
        review_v2_pretest(replace(dataset, provenance={}), selection_dir, bootstrap_samples=100)

    archive = selection_dir / "oof-predictions.npz"
    with archive.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="archive hash mismatch"):
        review_v2_pretest(dataset, selection_dir, bootstrap_samples=100)


def test_v2_review_cli_writes_an_immutable_private_report(tmp_path: Path) -> None:
    dataset, selection_dir = _fixture(tmp_path)
    dataset_dir = dataset.save(tmp_path / "processed")
    output_dir = tmp_path / "private-reviews"
    arguments = [
        "v2-pretest-review",
        "--dataset-dir",
        str(dataset_dir),
        "--selection-dir",
        str(selection_dir),
        "--output-dir",
        str(output_dir),
        "--bootstrap-samples",
        "100",
    ]

    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 0, result.output
    report_path = output_dir / dataset.dataset_id / "pretest-review.json"
    report = orjson.loads(report_path.read_bytes())
    recorded = report.pop("review_sha256")
    assert recorded == hashlib.sha256(orjson.dumps(report, option=orjson.OPT_SORT_KEYS)).hexdigest()
    assert CliRunner().invoke(app, arguments).exit_code != 0
