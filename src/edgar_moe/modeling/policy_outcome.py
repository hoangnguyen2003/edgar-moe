"""Private paired OOF review of the legacy and duration-aware XBRL policies.

Both selections were made on the same development folds after frozen v1 was
known. This module never indexes locked labels and cannot establish independent
skill or a tradable strategy. Dataset and selection identities are checked
before any model comparison is calculated.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import orjson

from edgar_moe.backtest.metrics import predictive_metrics
from edgar_moe.data.storage import sha256_file
from edgar_moe.features.dataset import ResearchDataset, dataset_xbrl_fact_policy
from edgar_moe.modeling.experiment import make_moe_specifications
from edgar_moe.modeling.policy_delta import verify_feature_policy_report
from edgar_moe.modeling.pretest_review import (
    _assert_metrics,
    _fold_record,
    _paired_delta_interval,
)
from edgar_moe.modeling.walk_forward import (
    PROTOCOL_REVISIONS,
    WalkForwardFold,
    make_walk_forward_folds,
)
from edgar_moe.settings import ResearchConfig

# Fixed before inspecting this paired outcome report. Text and market are
# negative controls; the configured MoE is not either study's selected winner.
FIXED_MODELS = (
    "Elastic Net",
    "Gradient-Boosted Tabular",
    "Text-Only Expert",
    "Fundamental-Only Expert",
    "Market-Only Expert",
    "Static Early-Fusion MLP",
    "Equal-Weight Expert Ensemble",
    "Shrinkage-Gated MoE h=64 dropout=0.15 gate=0.25",
)
SIMPLE_MODELS = (
    "Elastic Net",
    "Gradient-Boosted Tabular",
    "Text-Only Expert",
    "Fundamental-Only Expert",
    "Market-Only Expert",
    "Static Early-Fusion MLP",
    "Equal-Weight Expert Ensemble",
)


def _expected_roster(config: ResearchConfig) -> list[str]:
    specifications = make_moe_specifications(config)
    residual = 1.0 - config.model.fundamental_anchor_weight
    return (
        [specification.name for specification in specifications]
        + list(SIMPLE_MODELS)
        + [
            f"Fundamental-Anchored MoE h={specification.hidden_dim} "
            f"dropout={specification.dropout:.2f} gate={specification.gate_strength:.2f} "
            f"moe={residual:.2f}"
            for specification in specifications
        ]
    )


@dataclass(frozen=True)
class _Selection:
    config: ResearchConfig
    folds: list[WalkForwardFold]
    indices: np.ndarray
    names: list[str]
    scores: np.ndarray
    metrics: dict[str, float]
    champion_name: str
    selection_hash: str
    oof_hash: str


def review_policy_outcomes(
    baseline: ResearchDataset,
    candidate: ResearchDataset,
    baseline_selection_dir: Path,
    candidate_selection_dir: Path,
    input_audit: dict[str, Any],
    *,
    baseline_manifest_sha256: str,
    candidate_manifest_sha256: str,
    bootstrap_samples: int = 1000,
    block_months: int = 2,
    seed: int = 42,
) -> dict[str, Any]:
    """Compare fixed models on aligned, hash-verified pre-test OOF events."""
    if not 100 <= bootstrap_samples <= 5000 or not 1 <= block_months <= 6:
        raise ValueError("bootstrap budget or calendar block length is outside reviewed bounds")
    verify_feature_policy_report(input_audit)
    if input_audit["status"] != "isolated_policy_change":
        raise ValueError("paired model review requires an isolated pre-test input change")
    if (
        input_audit["baseline_dataset_id"] != baseline.dataset_id
        or input_audit["candidate_dataset_id"] != candidate.dataset_id
        or input_audit["baseline_manifest_sha256"] != baseline_manifest_sha256
        or input_audit["candidate_manifest_sha256"] != candidate_manifest_sha256
        or input_audit["baseline_source_manifest_hash"] != baseline.source_manifest_hash
        or input_audit["candidate_source_manifest_hash"] != candidate.source_manifest_hash
    ):
        raise ValueError("input audit does not bind the paired datasets and manifests")
    boundary = date.fromisoformat(input_audit["test_start"])
    legacy = _load_selection(baseline, baseline_selection_dir, "legacy_v1", boundary)
    revised = _load_selection(candidate, candidate_selection_dir, "duration_aware_v2", boundary)
    legacy_config = legacy.config.model_dump(mode="json")
    revised_config = revised.config.model_dump(mode="json")
    legacy_config["features"]["xbrl_fact_policy"] = "duration_aware_v2"
    if legacy_config != revised_config:
        raise ValueError("selection configurations differ beyond the XBRL policy")
    if legacy.names != revised.names or not set(FIXED_MODELS).issubset(legacy.names):
        raise ValueError("paired selections lack an identical full model roster")

    legacy_ids = baseline.events.iloc[legacy.indices]["event_id"].astype(str).tolist()
    revised_ids = candidate.events.iloc[revised.indices]["event_id"].astype(str).tolist()
    if (
        len(set(legacy_ids)) != len(legacy_ids)
        or len(set(revised_ids)) != len(revised_ids)
        or set(legacy_ids) != set(revised_ids)
    ):
        raise ValueError("paired OOF event identities differ or are duplicated")
    legacy_by_id = dict(zip(legacy_ids, legacy.indices, strict=True))
    aligned_legacy = np.asarray([legacy_by_id[event_id] for event_id in revised_ids])
    if not np.array_equal(baseline.target[aligned_legacy], candidate.target[revised.indices]):
        raise ValueError("paired OOF labels differ")
    for old_fold, new_fold in zip(legacy.folds, revised.folds, strict=True):
        old_ids = set(baseline.events.iloc[old_fold.validation]["event_id"].astype(str))
        new_ids = set(candidate.events.iloc[new_fold.validation]["event_id"].astype(str))
        if old_fold.name != new_fold.name or old_ids != new_ids:
            raise ValueError("paired validation folds differ")

    # Align scores into candidate row order. Only OOF positions are populated;
    # every locked or otherwise unscored position remains NaN.
    aligned_scores = np.full_like(revised.scores, np.nan)
    aligned_scores[revised.indices] = legacy.scores[aligned_legacy]
    controls_equal = all(
        np.array_equal(
            aligned_scores[revised.indices, revised.names.index(name)],
            revised.scores[revised.indices, revised.names.index(name)],
        )
        for name in ("Text-Only Expert", "Market-Only Expert")
    )
    models: list[dict[str, Any]] = []
    for name in FIXED_MODELS:
        column = revised.names.index(name)
        delta, interval = _paired_delta_interval(
            candidate,
            revised.folds,
            revised.scores[:, column],
            aligned_scores[:, column],
            samples=bootstrap_samples,
            block_months=block_months,
            seed=seed,
        )
        models.append(
            {
                "name": name,
                "baseline_weighted_rank_ic": legacy.metrics[name],
                "candidate_weighted_rank_ic": revised.metrics[name],
                "candidate_minus_baseline_rank_ic": delta,
                "delta_interval_95": interval,
            }
        )
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "pretest_development_only" if controls_equal else "runtime_control_mismatch",
        "input_audit_hash": input_audit["report_hash"],
        "baseline_dataset_id": baseline.dataset_id,
        "candidate_dataset_id": candidate.dataset_id,
        "baseline_manifest_sha256": baseline_manifest_sha256,
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "baseline_selection_sha256": legacy.selection_hash,
        "candidate_selection_sha256": revised.selection_hash,
        "baseline_oof_sha256": legacy.oof_hash,
        "candidate_oof_sha256": revised.oof_hash,
        "test_start": boundary.isoformat(),
        "oof_events": len(revised.indices),
        "folds": [
            {"name": fold.name, "validation_events": len(fold.validation)} for fold in revised.folds
        ],
        "model_count": len(revised.names),
        "baseline_champion": legacy.champion_name,
        "candidate_champion": revised.champion_name,
        "negative_controls_equal": controls_equal,
        "fixed_model_comparisons": models,
        "uncertainty": {
            "method": "paired_calendar_month_moving_block_within_fold",
            "block_months": block_months,
            "resamples": bootstrap_samples,
            "seed": seed,
            "conditional_on_selection": True,
        },
        "locked_test_predictions": 0,
        "interpretation": (
            "Development-fold policy comparisons only; frozen v1 was known before either "
            "reconstruction, and both champions were selected on these same folds. "
            "Intervals are descriptive, not independent tests or alpha evidence."
        ),
    }
    report["report_hash"] = hashlib.sha256(
        orjson.dumps(report, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    return report


def _load_selection(
    dataset: ResearchDataset,
    directory: Path,
    expected_policy: str,
    boundary: date,
) -> _Selection:
    if dataset_xbrl_fact_policy(dataset) != expected_policy:
        raise ValueError("dataset XBRL policy differs from the paired study")
    payload = orjson.loads((directory / "walk-forward-selection.json").read_bytes())
    if not isinstance(payload, dict):
        raise ValueError("selection must be a JSON object")
    recorded_hash = payload.get("selection_hash")
    unsigned = {key: value for key, value in payload.items() if key != "selection_hash"}
    observed_hash = hashlib.sha256(orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)).hexdigest()
    if recorded_hash != observed_hash or not isinstance(recorded_hash, str):
        raise ValueError("walk-forward selection hash mismatch")
    if payload.get("dataset_id") != dataset.dataset_id:
        raise ValueError("selection and dataset identities differ")
    if payload.get("protocol_revisions") != PROTOCOL_REVISIONS:
        raise ValueError("selection lacks the reviewed post-v1 protocol")
    if (
        payload.get("locked_test_evaluated") is not False
        or payload.get("locked_test_prediction_count") != 0
    ):
        raise ValueError("selection is not pre-test-only")
    config = ResearchConfig.model_validate(payload["configuration"])
    if config.features.xbrl_fact_policy != expected_policy:
        raise ValueError("selection XBRL policy differs from its dataset")
    if config.evaluation.test_start != boundary.isoformat():
        raise ValueError("selection locked-test boundary differs from the input audit")
    folds = make_walk_forward_folds(dataset, config)
    if payload.get("folds") != [_fold_record(fold) for fold in folds]:
        raise ValueError("saved fold boundaries differ from the dataset")
    indices = np.concatenate([fold.validation for fold in folds])
    archive_path = directory / "oof-predictions.npz"
    oof_hash = sha256_file(archive_path)
    if payload.get("oof_predictions_sha256") != oof_hash:
        raise ValueError("out-of-fold score archive hash mismatch")
    with np.load(archive_path, allow_pickle=False) as archive:
        names = archive["model_names"].tolist()
        scores = np.asarray(archive["predictions"], dtype=np.float64)
        saved_indices = np.asarray(archive["oof_indices"], dtype=np.int64)
    models = payload.get("models")
    if not isinstance(models, list) or not all(isinstance(item, dict) for item in models):
        raise ValueError("selection model roster is invalid")
    if (
        not isinstance(names, list)
        or len(names) != len(set(names))
        or names != [item.get("name") for item in models]
        or names != _expected_roster(config)
        or scores.shape != (len(dataset.events), len(names))
        or not np.array_equal(saved_indices, indices)
    ):
        raise ValueError("score archive and selection roster do not align")
    outside = np.ones(len(dataset.events), dtype=bool)
    outside[indices] = False
    if not np.isnan(scores[outside]).all() or not np.isfinite(scores[indices]).all():
        raise ValueError("score archive contains missing OOF or non-OOF predictions")
    if not np.isfinite(dataset.target[indices]).all():
        raise ValueError("out-of-fold labels are incomplete")
    champion = payload.get("champion")
    selected = [item for item in models if item.get("selected") is True]
    if not isinstance(champion, dict) or len(selected) != 1 or selected[0] != champion:
        raise ValueError("selection has no unique champion")
    metrics: dict[str, float] = {}
    for position, model in enumerate(models):
        fold_metrics = {
            fold.name: predictive_metrics(
                dataset.target[fold.validation], scores[fold.validation, position]
            )
            for fold in folds
        }
        pooled = predictive_metrics(dataset.target[indices], scores[indices, position])
        weighted = float(
            np.average(
                [fold_metrics[fold.name]["rank_ic"] for fold in folds],
                weights=[len(fold.validation) for fold in folds],
            )
        )
        _assert_metrics(model, fold_metrics, pooled, weighted)
        metrics[str(model["name"])] = weighted
    return _Selection(
        config=config,
        folds=folds,
        indices=indices,
        names=names,
        scores=scores,
        metrics=metrics,
        champion_name=str(champion["name"]),
        selection_hash=recorded_hash,
        oof_hash=oof_hash,
    )


def verify_policy_outcome_report(report: dict[str, Any]) -> None:
    """Reject altered or row-level output from the private aggregate report."""
    expected = {
        "schema_version",
        "status",
        "input_audit_hash",
        "baseline_dataset_id",
        "candidate_dataset_id",
        "baseline_manifest_sha256",
        "candidate_manifest_sha256",
        "baseline_selection_sha256",
        "candidate_selection_sha256",
        "baseline_oof_sha256",
        "candidate_oof_sha256",
        "test_start",
        "oof_events",
        "folds",
        "model_count",
        "baseline_champion",
        "candidate_champion",
        "negative_controls_equal",
        "fixed_model_comparisons",
        "uncertainty",
        "locked_test_predictions",
        "interpretation",
        "report_hash",
    }
    if set(report) != expected or report.get("schema_version") != 1:
        raise ValueError("policy-outcome report fields are invalid")
    if report.get("status") not in {"pretest_development_only", "runtime_control_mismatch"}:
        raise ValueError("policy-outcome status is invalid")
    if report.get("negative_controls_equal") is not (
        report["status"] == "pretest_development_only"
    ):
        raise ValueError("policy-outcome negative-control status is inconsistent")
    if report.get("locked_test_predictions") != 0:
        raise ValueError("policy-outcome report includes locked predictions")
    for field in ("baseline_dataset_id", "candidate_dataset_id"):
        if (
            not isinstance(report.get(field), str)
            or re.fullmatch(r"research-\d{4}-\d{2}-\d{2}-[0-9a-f]{12}", report[field]) is None
        ):
            raise ValueError(f"policy-outcome {field} is invalid")
    if report["baseline_dataset_id"] == report["candidate_dataset_id"]:
        raise ValueError("policy-outcome dataset identities must differ")
    try:
        date.fromisoformat(report["test_start"])
    except (TypeError, ValueError) as error:
        raise ValueError("policy-outcome test boundary is invalid") from error
    if type(report.get("oof_events")) is not int or report["oof_events"] < 1:
        raise ValueError("policy-outcome OOF count is invalid")
    if type(report.get("model_count")) is not int or report["model_count"] < len(FIXED_MODELS):
        raise ValueError("policy-outcome model count is invalid")
    folds = report.get("folds")
    if (
        not isinstance(folds, list)
        or not folds
        or any(
            not isinstance(fold, dict)
            or set(fold) != {"name", "validation_events"}
            or not isinstance(fold["name"], str)
            or re.fullmatch(r"validation-\d{4}", fold["name"]) is None
            or type(fold["validation_events"]) is not int
            or fold["validation_events"] < 1
            for fold in folds
        )
    ):
        raise ValueError("policy-outcome fold summary is invalid")
    if (
        len({fold["name"] for fold in folds}) != len(folds)
        or sum(fold["validation_events"] for fold in folds) != report["oof_events"]
    ):
        raise ValueError("policy-outcome fold counts do not match OOF count")
    for field in ("baseline_champion", "candidate_champion"):
        if not isinstance(report.get(field), str) or len(report[field]) > 120:
            raise ValueError(f"policy-outcome {field} is invalid")
    comparisons = report.get("fixed_model_comparisons")
    if not isinstance(comparisons, list) or any(not isinstance(item, dict) for item in comparisons):
        raise ValueError("policy-outcome fixed model roster is invalid")
    if [item.get("name") for item in comparisons] != list(FIXED_MODELS):
        raise ValueError("policy-outcome fixed model roster is invalid")
    allowed_model_fields = {
        "name",
        "baseline_weighted_rank_ic",
        "candidate_weighted_rank_ic",
        "candidate_minus_baseline_rank_ic",
        "delta_interval_95",
    }
    if any(not isinstance(item, dict) or set(item) != allowed_model_fields for item in comparisons):
        raise ValueError("policy-outcome model fields are invalid")
    for item in comparisons:
        for field in (
            "baseline_weighted_rank_ic",
            "candidate_weighted_rank_ic",
            "candidate_minus_baseline_rank_ic",
        ):
            value = item[field]
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 2:
                raise ValueError("policy-outcome rank-IC value is invalid")
        observed_delta = item["candidate_weighted_rank_ic"] - item["baseline_weighted_rank_ic"]
        if not math.isclose(observed_delta, item["candidate_minus_baseline_rank_ic"], abs_tol=1e-9):
            raise ValueError("policy-outcome rank-IC difference is inconsistent")
        interval = item["delta_interval_95"]
        if not isinstance(interval, dict) or set(interval) != {
            "status",
            "low",
            "high",
            "valid_resamples",
            "calendar_months_per_fold",
        }:
            raise ValueError("policy-outcome interval fields are invalid")
        if (
            interval["status"] != "ready"
            or any(
                type(interval[field]) not in (int, float) or not math.isfinite(interval[field])
                for field in ("low", "high")
            )
            or interval["low"] > interval["high"]
        ):
            raise ValueError("policy-outcome interval is invalid")
        if (
            type(interval["valid_resamples"]) is not int
            or not 100 <= interval["valid_resamples"] <= 5000
        ):
            raise ValueError("policy-outcome interval resample count is invalid")
        months = interval["calendar_months_per_fold"]
        if (
            not isinstance(months, list)
            or len(months) != len(folds)
            or any(type(month) is not int or month < 1 for month in months)
        ):
            raise ValueError("policy-outcome calendar block count is invalid")
    uncertainty = report.get("uncertainty")
    if (
        not isinstance(uncertainty, dict)
        or set(uncertainty)
        != {"method", "block_months", "resamples", "seed", "conditional_on_selection"}
        or uncertainty["method"] != "paired_calendar_month_moving_block_within_fold"
        or uncertainty["conditional_on_selection"] is not True
    ):
        raise ValueError("policy-outcome uncertainty contract is invalid")
    if (
        type(uncertainty["block_months"]) is not int
        or not 1 <= uncertainty["block_months"] <= 6
        or type(uncertainty["resamples"]) is not int
        or not 100 <= uncertainty["resamples"] <= 5000
        or type(uncertainty["seed"]) is not int
    ):
        raise ValueError("policy-outcome uncertainty parameters are invalid")
    if any(
        item["delta_interval_95"]["valid_resamples"] > uncertainty["resamples"]
        for item in comparisons
    ):
        raise ValueError("policy-outcome interval exceeds bootstrap budget")
    if not isinstance(report.get("interpretation"), str) or report["interpretation"] != (
        "Development-fold policy comparisons only; frozen v1 was known before either "
        "reconstruction, and both champions were selected on these same folds. "
        "Intervals are descriptive, not independent tests or alpha evidence."
    ):
        raise ValueError("policy-outcome interpretation is invalid")
    for field in (
        "input_audit_hash",
        "baseline_manifest_sha256",
        "candidate_manifest_sha256",
        "baseline_selection_sha256",
        "candidate_selection_sha256",
        "baseline_oof_sha256",
        "candidate_oof_sha256",
        "report_hash",
    ):
        digest = report.get(field)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(f"policy-outcome {field} is invalid")
    unsigned = {key: value for key, value in report.items() if key != "report_hash"}
    if (
        report["report_hash"]
        != hashlib.sha256(orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)).hexdigest()
    ):
        raise ValueError("policy-outcome report hash does not match content")
