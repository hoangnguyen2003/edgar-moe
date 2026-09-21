from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pandas as pd

from edgar_moe.backtest.metrics import predictive_metrics
from edgar_moe.data.storage import sha256_file
from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.modeling.comparisons import train_comparisons
from edgar_moe.modeling.experiment import (
    ArrayTransform,
    MoESpecification,
    make_moe_specifications,
)
from edgar_moe.modeling.moe import RegimeGatedMoE, torch
from edgar_moe.modeling.preprocess import MultimodalPreprocessor
from edgar_moe.modeling.train import predict_moe, set_deterministic_seed, train_moe
from edgar_moe.settings import ResearchConfig

SELECTION_RULE = (
    "maximize worst-fold rank IC; then validation-count-weighted mean fold rank IC; "
    "then minimize pooled out-of-fold RMSE"
)
# Share of each fold's training window, by acceptance time, held out for MoE
# early stopping so the scored validation fold never picks the stopping epoch.
EARLY_STOPPING_FRACTION = 0.15
# Recorded in selection artifacts; frozen v1 predates both revisions (it early-
# stopped on the scored fold and fit Elastic Net baselines on raw returns).
PROTOCOL_REVISIONS = {
    "moe_early_stopping": "purged chronological holdout from each training window",
    "linear_baselines": "elastic net fit on a standardized target",
}


@dataclass(frozen=True)
class WalkForwardFold:
    name: str
    validation_year: int
    train: np.ndarray
    validation: np.ndarray
    train_label_cutoff: str
    validation_start: str
    validation_end: str


@dataclass(frozen=True)
class WalkForwardModelResult:
    name: str
    family: str
    parameters: dict[str, int | float]
    fold_metrics: dict[str, dict[str, float]]
    aggregate_metrics: dict[str, float]
    worst_fold_rank_ic: float
    oof_predictions: np.ndarray


@dataclass(frozen=True)
class WalkForwardStudyResult:
    run_id: str
    dataset_id: str
    folds: list[WalkForwardFold]
    models: list[WalkForwardModelResult]
    champion: WalkForwardModelResult
    oof_indices: np.ndarray
    locked_indices: np.ndarray
    locked_test_start: str
    selection_rule: str = SELECTION_RULE
    locked_test_evaluated: bool = False


@dataclass
class _ModelAccumulator:
    name: str
    family: str
    parameters: dict[str, int | float]
    predictions: np.ndarray
    fold_metrics: dict[str, dict[str, float]] = field(default_factory=dict)


def make_walk_forward_folds(
    dataset: ResearchDataset,
    config: ResearchConfig,
) -> list[WalkForwardFold]:
    """Build expanding folds that end strictly before the locked-test boundary."""
    years = sorted(set(config.evaluation.walk_forward_years))
    if not years:
        raise ValueError("walk_forward_years must contain at least one year")
    accepted = pd.to_datetime(dataset.events["accepted_at"], utc=True)
    horizon = pd.to_datetime(dataset.events["horizon_at"], utc=True)
    test_start = pd.Timestamp(config.evaluation.test_start, tz="UTC")
    pretest = (accepted < test_start).to_numpy()
    finite_target = np.zeros(len(dataset.events), dtype=bool)
    pretest_indices = np.flatnonzero(pretest)
    finite_target[pretest_indices] = np.isfinite(dataset.target[pretest_indices])
    minimum = config.evaluation.minimum_split_events
    folds: list[WalkForwardFold] = []
    observed_validation = np.zeros(len(dataset.events), dtype=bool)

    for year in years:
        validation_start = pd.Timestamp(year=year, month=1, day=1, tz="UTC")
        validation_end = pd.Timestamp(year=year + 1, month=1, day=1, tz="UTC")
        if validation_end > test_start:
            raise ValueError(
                f"Walk-forward year {year} reaches the locked test beginning "
                f"{config.evaluation.test_start}"
            )
        train = np.flatnonzero(
            ((accepted < validation_start) & (horizon < validation_start)).to_numpy()
            & finite_target
        )
        validation = np.flatnonzero(
            (
                (accepted >= validation_start)
                & (accepted < validation_end)
                & (horizon < validation_end)
            ).to_numpy()
            & finite_target
        )
        counts = {"train": len(train), "validation": len(validation)}
        if min(counts.values()) < minimum:
            raise ValueError(
                f"Walk-forward fold {year} needs at least {minimum} matured events per "
                f"partition; observed {counts}"
            )
        if observed_validation[validation].any():
            raise ValueError(f"Walk-forward fold {year} overlaps an earlier validation fold")
        observed_validation[validation] = True
        if (accepted.iloc[validation] >= test_start).any():
            raise AssertionError("Locked-test event entered a walk-forward fold")
        folds.append(
            WalkForwardFold(
                name=f"validation-{year}",
                validation_year=year,
                train=train,
                validation=validation,
                train_label_cutoff=validation_start.isoformat(),
                validation_start=validation_start.isoformat(),
                validation_end=validation_end.isoformat(),
            )
        )
    return folds


def run_walk_forward_study(
    dataset: ResearchDataset,
    *,
    config: ResearchConfig | None = None,
    max_epochs: int | None = None,
    maximum_candidates: int | None = None,
    device: str | None = "cpu",
) -> WalkForwardStudyResult:
    """Select a stable champion using pre-test expanding-window predictions only."""
    if torch is None:
        raise RuntimeError("Install research dependencies with `uv sync --extra research`")
    config = config or ResearchConfig()
    folds = make_walk_forward_folds(dataset, config)
    specifications = make_moe_specifications(config, maximum_candidates)
    epochs = max_epochs if max_epochs is not None else config.model.max_epochs
    accumulators: dict[str, _ModelAccumulator] = {}

    for specification in specifications:
        accumulators[specification.name] = _ModelAccumulator(
            name=specification.name,
            family="multimodal",
            parameters=_specification_parameters(specification),
            predictions=np.full(len(dataset.events), np.nan, dtype=np.float64),
        )

    for fold in folds:
        train_values, validation_values, target_mean, target_std = _prepare_fold(
            dataset,
            fold,
        )
        inner_positions, holdout_positions = _early_stopping_split(dataset, fold)
        inner_values = _subset(train_values, inner_positions)
        holdout_values = _subset(train_values, holdout_positions)
        validation_target = dataset.target[fold.validation]
        for specification in specifications:
            set_deterministic_seed(config.project.random_seed)
            model = RegimeGatedMoE(
                text_dim=train_values["text"].shape[1],
                fundamental_dim=train_values["fundamental"].shape[1],
                market_dim=train_values["market"].shape[1],
                regime_dim=train_values["regime"].shape[1],
                hidden_dim=specification.hidden_dim,
                expert_dim=config.model.expert_dim,
                dropout=specification.dropout,
                gate_strength=specification.gate_strength,
            )
            training = train_moe(
                model,
                inner_values,
                holdout_values,
                learning_rate=config.model.learning_rate,
                weight_decay=config.model.weight_decay,
                entropy_regularization=config.model.entropy_regularization,
                expert_auxiliary_weight=config.model.expert_auxiliary_weight,
                correlation_regularization=config.model.correlation_regularization,
                batch_size=config.model.batch_size,
                max_epochs=epochs,
                patience=config.model.patience,
                seed=config.project.random_seed,
                device=device,
            )
            standardized = predict_moe(
                training.model,
                validation_values,
                device=device,
            ).scores
            predictions = standardized * target_std + target_mean
            accumulator = accumulators[specification.name]
            accumulator.predictions[fold.validation] = predictions
            accumulator.fold_metrics[fold.name] = predictive_metrics(
                validation_target,
                predictions,
            )
            accumulator.parameters[f"best_epoch_{fold.validation_year}"] = training.best_epoch

        comparison_train = {
            **train_values,
            "target": dataset.target[fold.train],
        }
        comparison_validation = {
            **validation_values,
            "target": validation_target,
        }
        comparisons = train_comparisons(
            comparison_train,
            comparison_validation,
            validation_target,
            test=None,
            seed=config.project.random_seed,
        )
        for comparison in comparisons:
            accumulator = accumulators.setdefault(
                comparison.name,
                _ModelAccumulator(
                    name=comparison.name,
                    family=comparison.family,
                    parameters={},
                    predictions=np.full(len(dataset.events), np.nan, dtype=np.float64),
                ),
            )
            accumulator.predictions[fold.validation] = comparison.validation_predictions
            accumulator.fold_metrics[fold.name] = comparison.validation_metrics

    _add_fundamental_anchored_models(
        accumulators,
        specifications,
        dataset,
        folds,
        anchor_weight=config.model.fundamental_anchor_weight,
    )

    oof_indices = np.concatenate([fold.validation for fold in folds])
    locked_start = pd.Timestamp(config.evaluation.test_start, tz="UTC")
    accepted = pd.to_datetime(dataset.events["accepted_at"], utc=True)
    locked_indices = np.flatnonzero((accepted >= locked_start).to_numpy())
    models = [
        _finalize_model(accumulator, dataset.target, folds, oof_indices)
        for accumulator in accumulators.values()
    ]
    if any(np.isfinite(model.oof_predictions[locked_indices]).any() for model in models):
        raise AssertionError("Walk-forward study produced a locked-test prediction")
    champion = min(models, key=_selection_key)
    run_payload = {
        "dataset_id": dataset.dataset_id,
        "years": [fold.validation_year for fold in folds],
        "candidate_names": [item.name for item in specifications],
        "fundamental_anchor_weight": config.model.fundamental_anchor_weight,
        "seed": config.project.random_seed,
        "selection_rule": SELECTION_RULE,
    }
    run_hash = _payload_hash(run_payload)[:12]
    return WalkForwardStudyResult(
        run_id=f"{dataset.dataset_id}-walk-forward-{run_hash}",
        dataset_id=dataset.dataset_id,
        folds=folds,
        models=models,
        champion=champion,
        oof_indices=oof_indices,
        locked_indices=locked_indices,
        locked_test_start=config.evaluation.test_start,
    )


def save_walk_forward_artifacts(
    result: WalkForwardStudyResult,
    output_root: str | Path,
    *,
    config: ResearchConfig,
) -> Path:
    """Freeze walk-forward evidence and its content hash without opening the test."""
    output = Path(output_root) / result.dataset_id
    output.mkdir(parents=True, exist_ok=True)
    predictions_path = output / "oof-predictions.npz"
    temporary_predictions = predictions_path.with_suffix(".npz.tmp")
    prediction_matrix = np.column_stack([model.oof_predictions for model in result.models])
    with temporary_predictions.open("wb") as stream:
        np.savez_compressed(
            stream,
            model_names=np.asarray([model.name for model in result.models], dtype=np.str_),
            predictions=prediction_matrix,
            oof_indices=result.oof_indices,
        )
    temporary_predictions.replace(predictions_path)
    payload: dict[str, Any] = {
        "run_id": result.run_id,
        "dataset_id": result.dataset_id,
        "configuration": config.model_dump(mode="json"),
        "protocol": "expanding-window pre-test model selection",
        "protocol_revisions": dict(PROTOCOL_REVISIONS),
        "selection_rule": result.selection_rule,
        "locked_test_start": result.locked_test_start,
        "locked_test_evaluated": result.locked_test_evaluated,
        "locked_test_prediction_count": 0,
        "oof_predictions_sha256": sha256_file(predictions_path),
        "folds": [_fold_record(fold) for fold in result.folds],
        "champion": _model_record(result.champion, selected=True),
        "models": [
            _model_record(model, selected=model.name == result.champion.name)
            for model in result.models
        ],
    }
    payload["selection_hash"] = _payload_hash(payload)
    selection_path = output / "walk-forward-selection.json"
    temporary_selection = selection_path.with_suffix(".json.tmp")
    temporary_selection.write_bytes(
        orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    )
    temporary_selection.replace(selection_path)
    return output


def _early_stopping_split(
    dataset: ResearchDataset,
    fold: WalkForwardFold,
) -> tuple[np.ndarray, np.ndarray]:
    """Split a fold's training window into purged inner-train and holdout positions.

    The holdout is the most recent ``EARLY_STOPPING_FRACTION`` of training events
    by acceptance time. Inner-train events must mature before the holdout starts,
    so no label overlaps it. Positions index into ``fold.train``.
    """
    accepted = pd.to_datetime(dataset.events["accepted_at"], utc=True).iloc[fold.train]
    horizon = pd.to_datetime(dataset.events["horizon_at"], utc=True).iloc[fold.train]
    holdout_count = max(1, math.ceil(len(fold.train) * EARLY_STOPPING_FRACTION))
    holdout_start = accepted.sort_values(kind="stable").iloc[-holdout_count]
    holdout = np.flatnonzero((accepted >= holdout_start).to_numpy())
    inner = np.flatnonzero((horizon < holdout_start).to_numpy())
    if not len(inner) or not len(holdout):
        raise ValueError(
            f"Walk-forward fold {fold.name} is too small for a purged early-stopping holdout"
        )
    return inner, holdout


def _subset(values: dict[str, np.ndarray], positions: np.ndarray) -> dict[str, np.ndarray]:
    return {name: array[positions] for name, array in values.items()}


def _prepare_fold(
    dataset: ResearchDataset,
    fold: WalkForwardFold,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], float, float]:
    train_modalities = {name: values[fold.train] for name, values in dataset.modalities.items()}
    validation_modalities = {
        name: values[fold.validation] for name, values in dataset.modalities.items()
    }
    preprocessor = MultimodalPreprocessor().fit(train_modalities)
    transformed_train, train_missing = preprocessor.transform(train_modalities)
    transformed_validation, validation_missing = preprocessor.transform(validation_modalities)
    regime_transform = ArrayTransform.fit(dataset.regime[fold.train])
    target_mean = float(np.mean(dataset.target[fold.train]))
    target_std = float(np.std(dataset.target[fold.train])) or 1.0
    train = {
        **transformed_train,
        "regime": regime_transform.transform(dataset.regime[fold.train]),
        "missing_mask": train_missing,
        "target": ((dataset.target[fold.train] - target_mean) / target_std).astype(np.float32),
    }
    validation = {
        **transformed_validation,
        "regime": regime_transform.transform(dataset.regime[fold.validation]),
        "missing_mask": validation_missing,
        "target": ((dataset.target[fold.validation] - target_mean) / target_std).astype(np.float32),
    }
    return train, validation, target_mean, target_std


def _add_fundamental_anchored_models(
    accumulators: dict[str, _ModelAccumulator],
    specifications: list[MoESpecification],
    dataset: ResearchDataset,
    folds: list[WalkForwardFold],
    *,
    anchor_weight: float,
) -> None:
    if not 0.0 < anchor_weight < 1.0:
        raise ValueError("fundamental_anchor_weight must be strictly between zero and one")
    fundamental = accumulators["Fundamental-Only Expert"]
    residual_weight = 1.0 - anchor_weight
    for specification in specifications:
        moe = accumulators[specification.name]
        name = (
            f"Fundamental-Anchored MoE h={specification.hidden_dim} "
            f"dropout={specification.dropout:.2f} gate={specification.gate_strength:.2f} "
            f"moe={residual_weight:.2f}"
        )
        predictions = anchor_weight * fundamental.predictions + residual_weight * moe.predictions
        fold_metrics = {
            fold.name: predictive_metrics(
                dataset.target[fold.validation],
                predictions[fold.validation],
            )
            for fold in folds
        }
        accumulators[name] = _ModelAccumulator(
            name=name,
            family="anchored_multimodal",
            parameters={
                **moe.parameters,
                "fundamental_anchor_weight": anchor_weight,
                "moe_residual_weight": residual_weight,
            },
            predictions=predictions,
            fold_metrics=fold_metrics,
        )


def _finalize_model(
    accumulator: _ModelAccumulator,
    target: np.ndarray,
    folds: list[WalkForwardFold],
    oof_indices: np.ndarray,
) -> WalkForwardModelResult:
    aggregate = predictive_metrics(
        target[oof_indices],
        accumulator.predictions[oof_indices],
    )
    fold_rank_values = [
        accumulator.fold_metrics[fold.name].get("rank_ic", float("nan")) for fold in folds
    ]
    if all(math.isfinite(value) for value in fold_rank_values):
        aggregate["rank_ic"] = float(
            np.average(
                fold_rank_values,
                weights=[len(fold.validation) for fold in folds],
            )
        )
    else:
        aggregate["rank_ic"] = float("nan")
    fold_rank_ics = [
        _finite_or(metrics.get("rank_ic"), -math.inf)
        for metrics in accumulator.fold_metrics.values()
    ]
    worst_fold = min(fold_rank_ics) if fold_rank_ics else -math.inf
    return WalkForwardModelResult(
        name=accumulator.name,
        family=accumulator.family,
        parameters=accumulator.parameters,
        fold_metrics=accumulator.fold_metrics,
        aggregate_metrics=aggregate,
        worst_fold_rank_ic=worst_fold,
        oof_predictions=accumulator.predictions,
    )


def _selection_key(model: WalkForwardModelResult) -> tuple[float, float, float, str]:
    worst = _finite_or(model.worst_fold_rank_ic, -math.inf)
    aggregate_rank = _finite_or(model.aggregate_metrics.get("rank_ic"), -math.inf)
    rmse = _finite_or(model.aggregate_metrics.get("rmse"), math.inf)
    return -worst, -aggregate_rank, rmse, model.name


def _specification_parameters(
    specification: MoESpecification,
) -> dict[str, int | float]:
    return {
        "hidden_dim": specification.hidden_dim,
        "dropout": specification.dropout,
        "gate_strength": specification.gate_strength,
    }


def _fold_record(fold: WalkForwardFold) -> dict[str, Any]:
    return {
        "name": fold.name,
        "validation_year": fold.validation_year,
        "train_events": len(fold.train),
        "validation_events": len(fold.validation),
        "train_label_cutoff": fold.train_label_cutoff,
        "validation_start": fold.validation_start,
        "validation_end": fold.validation_end,
    }


def _model_record(
    model: WalkForwardModelResult,
    *,
    selected: bool,
) -> dict[str, Any]:
    return {
        "name": model.name,
        "family": model.family,
        "parameters": model.parameters,
        "selected": selected,
        "worst_fold_rank_ic": _finite_or_none(model.worst_fold_rank_ic),
        "aggregate_metrics": _finite_mapping(model.aggregate_metrics),
        "fold_metrics": {
            name: _finite_mapping(metrics) for name, metrics in model.fold_metrics.items()
        },
    }


def _finite_mapping(values: dict[str, float]) -> dict[str, float | None]:
    return {name: _finite_or_none(value) for name, value in values.items()}


def _finite_or_none(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def _finite_or(value: float | None, fallback: float) -> float:
    return float(value) if value is not None and math.isfinite(float(value)) else fallback


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()
