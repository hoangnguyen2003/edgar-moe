from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pandas as pd

from edgar_moe.backtest.engine import BacktestResult, run_event_backtest
from edgar_moe.backtest.metrics import predictive_metrics
from edgar_moe.data.storage import sha256_file
from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.modeling.comparisons import train_comparisons
from edgar_moe.modeling.experiment import ArrayTransform, portfolio_outputs
from edgar_moe.modeling.moe import RegimeGatedMoE, torch
from edgar_moe.modeling.preprocess import MultimodalPreprocessor
from edgar_moe.modeling.train import (
    TrainingResult,
    predict_moe,
    set_deterministic_seed,
    train_moe,
)
from edgar_moe.settings import ResearchConfig


@dataclass(frozen=True)
class FrozenSplit:
    train: np.ndarray
    test: np.ndarray


@dataclass(frozen=True)
class FrozenEvaluationResult:
    run_id: str
    dataset_id: str
    selection_hash: str
    selection_payload: dict[str, Any]
    config: ResearchConfig
    champion_name: str
    champion_family: str
    champion_parameters: dict[str, int | float]
    split: FrozenSplit
    scores: np.ndarray
    ranks: np.ndarray
    fundamental_scores: np.ndarray
    expert_weights: np.ndarray | None
    expert_predictions: np.ndarray | None
    test_metrics: dict[str, float]
    component_metrics: dict[str, dict[str, float]]
    portfolio_scenarios: list[dict[str, float | int | None]]
    equity_curves: dict[str, list[dict[str, Any]]]
    backtest: BacktestResult
    preprocessor: MultimodalPreprocessor
    regime_transform: ArrayTransform
    target_mean: float
    target_std: float
    moe_training: TrainingResult | None
    fundamental_model: Any | None
    comparison_models: dict[str, Any]


def verify_frozen_selection(
    selection_path: str | Path,
    *,
    dataset_id: str,
    confirmation_hash: str,
) -> dict[str, Any]:
    """Verify the exact pre-test artifact before any locked target is accessed."""
    path = Path(selection_path)
    payload = orjson.loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError("Walk-forward selection artifact must contain a JSON object")
    stored_hash = str(payload.get("selection_hash", ""))
    unsigned = dict(payload)
    unsigned.pop("selection_hash", None)
    computed_hash = _payload_hash(unsigned)
    if not stored_hash or stored_hash != computed_hash:
        raise ValueError("Walk-forward selection hash verification failed")
    if confirmation_hash != stored_hash:
        raise ValueError("Confirmation hash does not match the frozen walk-forward selection")
    if str(payload.get("dataset_id")) != dataset_id:
        raise ValueError("Frozen selection belongs to a different dataset")
    if payload.get("locked_test_evaluated") is not False:
        raise ValueError("Selection artifact does not certify a sealed locked test")
    if int(payload.get("locked_test_prediction_count", -1)) != 0:
        raise ValueError("Selection artifact contains locked-test predictions")
    predictions_path = path.parent / "oof-predictions.npz"
    if not predictions_path.exists():
        raise FileNotFoundError(f"Missing frozen OOF predictions: {predictions_path}")
    if sha256_file(predictions_path) != str(payload.get("oof_predictions_sha256")):
        raise ValueError("Frozen OOF prediction hash verification failed")
    return payload


def evaluate_frozen_selection(
    dataset: ResearchDataset,
    selection_path: str | Path,
    *,
    confirmation_hash: str,
    device: str | None = "cpu",
) -> FrozenEvaluationResult:
    """Refit only the frozen champion, then explicitly evaluate the locked period."""
    if torch is None:
        raise RuntimeError("Install research dependencies with `uv sync --extra research`")
    selection = verify_frozen_selection(
        selection_path,
        dataset_id=dataset.dataset_id,
        confirmation_hash=confirmation_hash,
    )
    config = ResearchConfig.model_validate(selection["configuration"])
    split = _make_frozen_split(dataset, config)
    train_values, test_values, preprocessor, regime_transform, target_mean, target_std = (
        _prepare_frozen_values(dataset, split)
    )
    test_target = dataset.target[split.test]
    comparison_train = {**train_values, "target": dataset.target[split.train]}
    comparison_test = {**test_values, "target": test_target}
    comparisons = train_comparisons(
        comparison_train,
        comparison_test,
        test_target,
        test=None,
        seed=config.project.random_seed,
    )
    comparison_by_name = {item.name: item for item in comparisons}
    champion = selection["champion"]
    champion_name = str(champion["name"])
    champion_family = str(champion["family"])
    champion_parameters = {
        str(name): value
        for name, value in dict(champion.get("parameters", {})).items()
        if isinstance(value, (int, float))
    }

    moe_training: TrainingResult | None = None
    moe_scores: np.ndarray | None = None
    test_weights: np.ndarray | None = None
    test_expert_predictions: np.ndarray | None = None
    if champion_family in {"multimodal", "anchored_multimodal"}:
        frozen_epochs = _frozen_epoch_count(champion_parameters, config)
        set_deterministic_seed(config.project.random_seed)
        model = RegimeGatedMoE(
            text_dim=train_values["text"].shape[1],
            fundamental_dim=train_values["fundamental"].shape[1],
            market_dim=train_values["market"].shape[1],
            regime_dim=train_values["regime"].shape[1],
            hidden_dim=int(champion_parameters["hidden_dim"]),
            expert_dim=config.model.expert_dim,
            dropout=float(champion_parameters["dropout"]),
            gate_strength=float(champion_parameters["gate_strength"]),
        )
        moe_training = train_moe(
            model,
            train_values,
            train_values,
            learning_rate=config.model.learning_rate,
            weight_decay=config.model.weight_decay,
            entropy_regularization=config.model.entropy_regularization,
            expert_auxiliary_weight=config.model.expert_auxiliary_weight,
            correlation_regularization=config.model.correlation_regularization,
            batch_size=config.model.batch_size,
            max_epochs=frozen_epochs,
            patience=frozen_epochs + 1,
            seed=config.project.random_seed,
            device=device,
            restore_best=False,
        )
        moe_prediction = predict_moe(moe_training.model, test_values, device=device)
        moe_scores = moe_prediction.scores * target_std + target_mean
        test_weights = moe_prediction.expert_weights
        test_expert_predictions = moe_prediction.expert_predictions * target_std + target_mean

    fundamental = comparison_by_name["Fundamental-Only Expert"]
    if champion_family == "anchored_multimodal":
        if moe_scores is None:
            raise AssertionError("Anchored champion is missing its MoE component")
        anchor_weight = float(champion_parameters["fundamental_anchor_weight"])
        residual_weight = float(champion_parameters["moe_residual_weight"])
        if not math.isclose(anchor_weight + residual_weight, 1.0, abs_tol=1e-9):
            raise ValueError("Frozen anchored weights do not sum to one")
        test_scores = (
            anchor_weight * fundamental.validation_predictions + residual_weight * moe_scores
        )
    elif champion_family == "multimodal":
        if moe_scores is None:
            raise AssertionError("MoE champion did not produce predictions")
        test_scores = moe_scores
    else:
        try:
            test_scores = comparison_by_name[champion_name].validation_predictions
        except KeyError as error:
            raise ValueError(f"Frozen champion family is not supported: {champion_name}") from error

    scores = np.full(len(dataset.events), np.nan, dtype=np.float64)
    scores[split.test] = test_scores
    ranks = np.full(len(dataset.events), np.nan, dtype=np.float64)
    ranks[split.test] = pd.Series(test_scores).rank(method="average", pct=True).to_numpy()
    fundamental_scores = np.full(len(dataset.events), np.nan, dtype=np.float64)
    fundamental_scores[split.test] = fundamental.validation_predictions
    expert_weights = _expand_test_values(test_weights, split.test, len(dataset.events))
    expert_predictions = _expand_test_values(
        test_expert_predictions,
        split.test,
        len(dataset.events),
    )
    test_metrics = predictive_metrics(test_target, test_scores)
    component_metrics = {item.name: item.validation_metrics for item in comparisons}
    if moe_scores is not None:
        component_metrics["Frozen MoE Component"] = predictive_metrics(
            test_target,
            moe_scores,
        )
    signals = dataset.events.iloc[split.test][
        [
            "entry_date",
            "exit_date",
            "security_id",
            "ticker",
            "beta",
            "industry_code",
        ]
    ].copy()
    signals["score"] = test_scores
    test_start = pd.Timestamp(config.evaluation.test_start)
    return_dates = pd.to_datetime(dataset.daily_returns["date"]).dt.tz_localize(None)
    daily_returns = dataset.daily_returns.loc[return_dates >= test_start]
    backtest = run_event_backtest(signals, daily_returns, config.portfolio)
    scenarios, curves = portfolio_outputs(
        backtest,
        config,
        seed=config.project.random_seed,
    )
    run_id = f"{selection['run_id']}-locked"
    return FrozenEvaluationResult(
        run_id=run_id,
        dataset_id=dataset.dataset_id,
        selection_hash=confirmation_hash,
        selection_payload=selection,
        config=config,
        champion_name=champion_name,
        champion_family=champion_family,
        champion_parameters=champion_parameters,
        split=split,
        scores=scores,
        ranks=ranks,
        fundamental_scores=fundamental_scores,
        expert_weights=expert_weights,
        expert_predictions=expert_predictions,
        test_metrics=test_metrics,
        component_metrics=component_metrics,
        portfolio_scenarios=scenarios,
        equity_curves=curves,
        backtest=backtest,
        preprocessor=preprocessor,
        regime_transform=regime_transform,
        target_mean=target_mean,
        target_std=target_std,
        moe_training=moe_training,
        fundamental_model=fundamental.model,
        comparison_models={item.name: item.model for item in comparisons if item.model is not None},
    )


def save_frozen_evaluation(
    result: FrozenEvaluationResult,
    output_root: str | Path,
    *,
    recovery_note: str | None = None,
) -> Path:
    """Persist the one-time locked result and refuse every overwrite."""
    output = Path(output_root) / result.dataset_id
    output.mkdir(parents=True, exist_ok=True)
    locked_path = output / "locked-test.json"
    if locked_path.exists():
        raise FileExistsError(f"Locked test already exists at {locked_path}; refusing to overwrite")
    model_path = output / "frozen-model.pt"
    model_payload: dict[str, Any] = {
        "run_id": result.run_id,
        "selection_hash": result.selection_hash,
        "champion_name": result.champion_name,
        "champion_family": result.champion_family,
        "champion_parameters": result.champion_parameters,
        "target_mean": result.target_mean,
        "target_std": result.target_std,
        "preprocessor": {
            name: {
                "medians": transform.medians,
                "mean": transform.scaler.mean_,
                "scale": transform.scaler.scale_,
            }
            for name, transform in result.preprocessor.transforms.items()
        },
        "regime": {
            "medians": result.regime_transform.medians,
            "mean": result.regime_transform.scaler.mean_,
            "scale": result.regime_transform.scaler.scale_,
        },
    }
    if result.moe_training is not None:
        model_payload["moe_state_dict"] = result.moe_training.model.state_dict()
        model_payload["moe_config"] = result.moe_training.model.export_config()
    if result.fundamental_model is not None:
        model_payload["fundamental_elastic_net"] = {
            "coef": np.asarray(result.fundamental_model.coef_),
            "intercept": float(result.fundamental_model.intercept_),
        }
    model_payload["comparison_models"] = result.comparison_models
    temporary_model = model_path.with_suffix(".pt.tmp")
    torch.save(model_payload, temporary_model)
    temporary_model.replace(model_path)

    scores_path = output / "locked-test-scores.npz"
    temporary_scores = scores_path.with_suffix(".npz.tmp")
    with temporary_scores.open("wb") as stream:
        np.savez_compressed(
            stream,
            test_indices=result.split.test,
            scores=result.scores[result.split.test],
            ranks=result.ranks[result.split.test],
        )
    temporary_scores.replace(scores_path)
    payload: dict[str, Any] = {
        "run_id": result.run_id,
        "dataset_id": result.dataset_id,
        "selection_hash": result.selection_hash,
        "champion_name": result.champion_name,
        "champion_family": result.champion_family,
        "champion_parameters": result.champion_parameters,
        "train_events": len(result.split.train),
        "locked_test_events": len(result.split.test),
        "primary_model": _finite_mapping(result.test_metrics),
        "components": {
            name: _finite_mapping(metrics) for name, metrics in result.component_metrics.items()
        },
        "portfolio_scenarios": result.portfolio_scenarios,
        "opening_audit": {
            "completed_attempt": 2 if recovery_note else 1,
            "recovery_note": recovery_note,
        },
        "model_sha256": sha256_file(model_path),
        "scores_sha256": sha256_file(scores_path),
    }
    payload["locked_test_hash"] = _payload_hash(payload)
    temporary_locked = locked_path.with_suffix(".json.tmp")
    temporary_locked.write_bytes(
        orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    )
    temporary_locked.replace(locked_path)
    return output


def _make_frozen_split(
    dataset: ResearchDataset,
    config: ResearchConfig,
) -> FrozenSplit:
    accepted = pd.to_datetime(dataset.events["accepted_at"], utc=True)
    horizon = pd.to_datetime(dataset.events["horizon_at"], utc=True)
    test_start = pd.Timestamp(config.evaluation.test_start, tz="UTC")
    as_of_end = pd.Timestamp(dataset.as_of, tz="UTC") + pd.Timedelta(days=1)
    train_candidates = np.flatnonzero(((accepted < test_start) & (horizon < test_start)).to_numpy())
    test_candidates = np.flatnonzero(((accepted >= test_start) & (horizon < as_of_end)).to_numpy())
    train = train_candidates[np.isfinite(dataset.target[train_candidates])]
    test = test_candidates[np.isfinite(dataset.target[test_candidates])]
    minimum = config.evaluation.minimum_split_events
    if len(train) < minimum or len(test) < minimum:
        raise ValueError(
            f"Frozen evaluation needs at least {minimum} train and test events; "
            f"observed train={len(train)}, test={len(test)}"
        )
    return FrozenSplit(train=train, test=test)


def _prepare_frozen_values(
    dataset: ResearchDataset,
    split: FrozenSplit,
) -> tuple[
    dict[str, np.ndarray],
    dict[str, np.ndarray],
    MultimodalPreprocessor,
    ArrayTransform,
    float,
    float,
]:
    train_modalities = {name: values[split.train] for name, values in dataset.modalities.items()}
    test_modalities = {name: values[split.test] for name, values in dataset.modalities.items()}
    preprocessor = MultimodalPreprocessor().fit(train_modalities)
    transformed_train, train_missing = preprocessor.transform(train_modalities)
    transformed_test, test_missing = preprocessor.transform(test_modalities)
    regime_transform = ArrayTransform.fit(dataset.regime[split.train])
    target_mean = float(np.mean(dataset.target[split.train]))
    target_std = float(np.std(dataset.target[split.train])) or 1.0
    train = {
        **transformed_train,
        "regime": regime_transform.transform(dataset.regime[split.train]),
        "missing_mask": train_missing,
        "target": ((dataset.target[split.train] - target_mean) / target_std).astype(np.float32),
    }
    test = {
        **transformed_test,
        "regime": regime_transform.transform(dataset.regime[split.test]),
        "missing_mask": test_missing,
        "target": ((dataset.target[split.test] - target_mean) / target_std).astype(np.float32),
    }
    return train, test, preprocessor, regime_transform, target_mean, target_std


def _frozen_epoch_count(
    parameters: dict[str, int | float],
    config: ResearchConfig,
) -> int:
    best_epochs = [
        int(value) + 1 for name, value in parameters.items() if name.startswith("best_epoch_")
    ]
    if not best_epochs:
        raise ValueError("Frozen MoE selection does not record fold best epochs")
    epochs = int(round(float(np.median(best_epochs))))
    return min(max(epochs, 1), config.model.max_epochs)


def _expand_test_values(
    values: np.ndarray | None,
    test_indices: np.ndarray,
    event_count: int,
) -> np.ndarray | None:
    if values is None:
        return None
    expanded = np.full((event_count, values.shape[1]), np.nan, dtype=np.float64)
    expanded[test_indices] = values
    return expanded


def _finite_mapping(values: dict[str, float]) -> dict[str, float | None]:
    return {
        name: float(value) if math.isfinite(float(value)) else None
        for name, value in values.items()
    }


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()
