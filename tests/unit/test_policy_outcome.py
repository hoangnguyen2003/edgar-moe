from __future__ import annotations

import hashlib
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from subprocess import run

import numpy as np
import orjson
import pandas as pd
import pytest

from edgar_moe.backtest.metrics import predictive_metrics
from edgar_moe.data.storage import sha256_file
from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.modeling.policy_delta import compare_feature_policies
from edgar_moe.modeling.policy_outcome import (
    FIXED_MODELS,
    _expected_roster,
    review_policy_outcomes,
    verify_policy_outcome_report,
)
from edgar_moe.modeling.walk_forward import PROTOCOL_REVISIONS, make_walk_forward_folds
from edgar_moe.settings import ResearchConfig

BASE_HASH = "b" * 64
CANDIDATE_HASH = "c" * 64


def _dataset(policy: str, *, locked_value: float) -> ResearchDataset:
    rows = []
    for year in (2022, 2023, 2024, 2025):
        for month in range(1, 13):
            for security in range(3):
                accepted = pd.Timestamp(year=year, month=month, day=10 + security, tz="UTC")
                rows.append(
                    {
                        "event_id": f"private-{year}-{month}-{security}",
                        "accepted_at": accepted,
                        "entry_at": accepted + pd.Timedelta(days=1),
                        "horizon_at": accepted + pd.Timedelta(days=20),
                        "entry_date": (accepted + pd.Timedelta(days=1)).date(),
                    }
                )
    events = pd.DataFrame(rows)
    target = np.random.default_rng(7).normal(0, 0.05, len(rows)).astype(np.float32)
    target[-36:] = locked_value
    fundamental = np.zeros((len(rows), 1), dtype=np.float32)
    if policy == "duration_aware_v2":
        fundamental[:108] = 1.0
    return ResearchDataset(
        dataset_id=f"research-2026-07-31-{'a' * 12 if policy == 'legacy_v1' else 'b' * 12}",
        as_of=date(2026, 7, 31),
        events=events,
        modalities={
            "text": np.zeros((len(rows), 1), dtype=np.float32),
            "fundamental": fundamental,
            "market": np.zeros((len(rows), 1), dtype=np.float32),
        },
        regime=np.zeros((len(rows), 1), dtype=np.float32),
        target=target,
        daily_returns=pd.DataFrame(),
        availability=pd.DataFrame(),
        feature_names={name: [name] for name in ("text", "fundamental", "market", "regime")},
        attrition={},
        source_manifest_hash="d" * 64,
        provenance={"xbrl_fact_policy": policy},
    )


def _selection(dataset: ResearchDataset, directory: Path, *, control_shift: bool = False) -> None:
    directory.mkdir(parents=True)
    config = ResearchConfig()
    config.features.xbrl_fact_policy = dataset.provenance["xbrl_fact_policy"]
    config.evaluation.minimum_split_events = 10
    config.model.candidate_hidden_dims = [64]
    config.model.candidate_dropouts = [0.15]
    config.model.candidate_gate_strengths = [0.25]
    folds = make_walk_forward_folds(dataset, config)
    indices = np.concatenate([fold.validation for fold in folds])
    names = _expected_roster(config)
    scores = np.full((len(dataset.events), len(names)), np.nan, dtype=np.float64)
    event_ids = dataset.events.iloc[indices]["event_id"].astype(str).tolist()
    sorted_ids = sorted(event_ids)
    for column, name in enumerate(names):
        rng = np.random.default_rng(100 + column)
        noise_by_id = dict(zip(sorted_ids, rng.normal(0, 0.04, len(indices)), strict=True))
        noise = np.asarray([noise_by_id[event_id] for event_id in event_ids])
        if dataset.provenance["xbrl_fact_policy"] == "duration_aware_v2" and name not in (
            "Text-Only Expert",
            "Market-Only Expert",
        ):
            extra_by_id = dict(zip(sorted_ids, rng.normal(0, 0.01, len(indices)), strict=True))
            noise += np.asarray([extra_by_id[event_id] for event_id in event_ids])
        if control_shift and name == "Text-Only Expert":
            extra_by_id = dict(zip(sorted_ids, rng.normal(0, 0.01, len(indices)), strict=True))
            noise += np.asarray([extra_by_id[event_id] for event_id in event_ids])
        scores[indices, column] = dataset.target[indices] + noise
    archive = directory / "oof-predictions.npz"
    np.savez_compressed(
        archive,
        model_names=np.asarray(names, dtype=np.str_),
        predictions=scores,
        oof_indices=indices,
    )
    models = []
    for column, name in enumerate(names):
        fold_metrics = {
            fold.name: predictive_metrics(
                dataset.target[fold.validation], scores[fold.validation, column]
            )
            for fold in folds
        }
        pooled = predictive_metrics(dataset.target[indices], scores[indices, column])
        weighted = float(
            np.average(
                [fold_metrics[fold.name]["rank_ic"] for fold in folds],
                weights=[len(fold.validation) for fold in folds],
            )
        )
        models.append(
            {
                "name": name,
                "family": "fixture",
                "parameters": {},
                "selected": column == 0,
                "worst_fold_rank_ic": min(item["rank_ic"] for item in fold_metrics.values()),
                "aggregate_metrics": {**pooled, "rank_ic": weighted},
                "fold_metrics": fold_metrics,
            }
        )
    payload = {
        "run_id": "fixture-policy-outcome",
        "dataset_id": dataset.dataset_id,
        "configuration": config.model_dump(mode="json"),
        "protocol_revisions": PROTOCOL_REVISIONS,
        "locked_test_start": config.evaluation.test_start,
        "locked_test_evaluated": False,
        "locked_test_prediction_count": 0,
        "oof_predictions_sha256": sha256_file(archive),
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
    (directory / "walk-forward-selection.json").write_bytes(
        orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    )


def _paired(tmp_path: Path, *, reorder: bool = False, control_shift: bool = False):
    baseline = _dataset("legacy_v1", locked_value=999.0)
    candidate = _dataset("duration_aware_v2", locked_value=-999.0)
    if reorder:
        order = np.arange(len(candidate.events))[::-1]
        candidate = replace(
            candidate,
            events=candidate.events.iloc[order].reset_index(drop=True),
            modalities={name: values[order] for name, values in candidate.modalities.items()},
            regime=candidate.regime[order],
            target=candidate.target[order],
        )
    audit = compare_feature_policies(
        baseline,
        candidate,
        test_start=date(2025, 1, 1),
        baseline_manifest_sha256=BASE_HASH,
        candidate_manifest_sha256=CANDIDATE_HASH,
    )
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    _selection(baseline, baseline_dir)
    _selection(candidate, candidate_dir, control_shift=control_shift)
    return baseline, candidate, baseline_dir, candidate_dir, audit


def _review(tmp_path: Path, *, reorder: bool = False, control_shift: bool = False):
    baseline, candidate, baseline_dir, candidate_dir, audit = _paired(
        tmp_path, reorder=reorder, control_shift=control_shift
    )
    return review_policy_outcomes(
        baseline,
        candidate,
        baseline_dir,
        candidate_dir,
        audit,
        baseline_manifest_sha256=BASE_HASH,
        candidate_manifest_sha256=CANDIDATE_HASH,
        bootstrap_samples=100,
    )


def test_paired_outcome_review_is_aggregate_only_and_ignores_locked_labels(tmp_path: Path) -> None:
    report = _review(tmp_path)

    assert report["status"] == "pretest_development_only"
    assert report["negative_controls_equal"] is True
    assert report["locked_test_predictions"] == 0
    assert report["oof_events"] == 70
    assert len(report["fixed_model_comparisons"]) == len(FIXED_MODELS)
    assert "private-" not in str(report)
    assert "999" not in str(report)
    verify_policy_outcome_report(report)


def test_fold_construction_does_not_index_cross_boundary_targets() -> None:
    dataset = _dataset("duration_aware_v2", locked_value=999.0)
    accepted = pd.to_datetime(dataset.events["accepted_at"], utc=True)
    horizon = pd.to_datetime(dataset.events["horizon_at"], utc=True)
    forbidden = set(
        np.flatnonzero(((accepted < "2025-01-01") & (horizon >= "2025-01-01")).to_numpy())
    )
    forbidden.update(np.flatnonzero((accepted >= "2025-01-01").to_numpy()))

    class GuardedTarget(np.ndarray):
        def __new__(cls, values: np.ndarray):
            return np.asarray(values).view(cls)

        def __getitem__(self, key):
            positions = np.arange(len(self))[key]
            if np.intersect1d(np.atleast_1d(positions), list(forbidden)).size:
                raise AssertionError("locked or cross-boundary target was indexed")
            return super().__getitem__(key)

    config = ResearchConfig()
    config.evaluation.minimum_split_events = 10
    folds = make_walk_forward_folds(replace(dataset, target=GuardedTarget(dataset.target)), config)

    assert len(forbidden) > 36
    assert all(not forbidden.intersection(fold.validation) for fold in folds)


def test_paired_outcome_review_aligns_reordered_events(tmp_path: Path) -> None:
    report = _review(tmp_path, reorder=True)

    assert report["status"] == "pretest_development_only"
    assert report["negative_controls_equal"] is True


def test_negative_control_mismatch_is_visible(tmp_path: Path) -> None:
    report = _review(tmp_path, control_shift=True)

    assert report["status"] == "runtime_control_mismatch"
    assert report["negative_controls_equal"] is False


def test_tampered_oof_archive_and_audit_identity_fail_closed(tmp_path: Path) -> None:
    baseline, candidate, baseline_dir, candidate_dir, audit = _paired(tmp_path)
    with (candidate_dir / "oof-predictions.npz").open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="archive hash mismatch"):
        review_policy_outcomes(
            baseline,
            candidate,
            baseline_dir,
            candidate_dir,
            audit,
            baseline_manifest_sha256=BASE_HASH,
            candidate_manifest_sha256=CANDIDATE_HASH,
            bootstrap_samples=100,
        )
    with pytest.raises(ValueError, match="manifests"):
        review_policy_outcomes(
            baseline,
            candidate,
            baseline_dir,
            candidate_dir,
            audit,
            baseline_manifest_sha256="e" * 64,
            candidate_manifest_sha256=CANDIDATE_HASH,
            bootstrap_samples=100,
        )


def test_report_verifier_rejects_tampering_and_extra_fields(tmp_path: Path) -> None:
    report = _review(tmp_path)
    changed = {**report, "oof_events": 999}
    with pytest.raises(ValueError, match="fold counts|hash"):
        verify_policy_outcome_report(changed)
    leaked = {**report, "private_rows": [{"event_id": "private-1"}]}
    with pytest.raises(ValueError, match="fields"):
        verify_policy_outcome_report(leaked)
    nested = {
        **report,
        "fixed_model_comparisons": [
            {**report["fixed_model_comparisons"][0], "private_event": "private-1"},
            *report["fixed_model_comparisons"][1:],
        ],
    }
    nested_unsigned = {key: value for key, value in nested.items() if key != "report_hash"}
    nested["report_hash"] = hashlib.sha256(
        orjson.dumps(nested_unsigned, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    with pytest.raises(ValueError, match="model fields"):
        verify_policy_outcome_report(nested)


def test_cli_rejects_public_output_path(tmp_path: Path) -> None:
    completed = run(
        [
            sys.executable,
            "scripts/review_paired_xbrl_outcomes.py",
            "--baseline-dataset",
            "unused-baseline",
            "--candidate-dataset",
            "unused-candidate",
            "--baseline-selection",
            "unused-baseline-selection",
            "--candidate-selection",
            "unused-candidate-selection",
            "--input-audit",
            "unused-audit",
            "--output",
            str(tmp_path / "public" / "review.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "ignored data/artifacts/v2-reviews" in completed.stderr
