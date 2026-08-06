from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pandas as pd
from sklearn.preprocessing import StandardScaler

from edgar_moe.backtest.engine import BacktestResult, run_event_backtest
from edgar_moe.backtest.metrics import (
    block_bootstrap_sharpe_interval,
    performance_metrics,
    predictive_metrics,
)
from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.modeling.comparisons import ComparisonResult, train_comparisons
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
class ResearchSplit:
    development: np.ndarray
    validation: np.ndarray
    test: np.ndarray


@dataclass(frozen=True)
class ArrayTransform:
    medians: np.ndarray
    scaler: StandardScaler

    @classmethod
    def fit(cls, values: np.ndarray) -> ArrayTransform:
        medians = np.nanmedian(values, axis=0)
        medians = np.where(np.isfinite(medians), medians, 0.0)
        imputed = np.where(np.isnan(values), medians, values)
        return cls(medians=medians, scaler=StandardScaler().fit(imputed))

    def transform(self, values: np.ndarray) -> np.ndarray:
        imputed = np.where(np.isnan(values), self.medians, values)
        return np.asarray(self.scaler.transform(imputed), dtype=np.float32)


@dataclass(frozen=True)
class MoESpecification:
    hidden_dim: int
    dropout: float
    gate_strength: float

    @property
    def name(self) -> str:
        return (
            f"Shrinkage-Gated MoE h={self.hidden_dim} "
            f"dropout={self.dropout:.2f} gate={self.gate_strength:.2f}"
        )


@dataclass(frozen=True)
class MoECandidateResult:
    name: str
    hidden_dim: int
    dropout: float
    gate_strength: float
    validation_metrics: dict[str, float]
    validation_scores: np.ndarray
    training: TrainingResult


@dataclass(frozen=True)
class AuthenticatedStudyResult:
    run_id: str
    dataset_id: str
    split: ResearchSplit
    selected_candidate: MoECandidateResult
    candidates: list[MoECandidateResult]
    comparisons: list[ComparisonResult]
    scores: np.ndarray
    ranks: np.ndarray
    expert_weights: np.ndarray
    expert_predictions: np.ndarray
    validation_metrics: dict[str, float]
    locked_test_metrics: dict[str, float] | None
    locked_comparison_metrics: dict[str, dict[str, float]]
    portfolio_scenarios: list[dict[str, float | int | None]]
    equity_curves: dict[str, list[dict[str, Any]]]
    backtest: BacktestResult | None
    locked_test_evaluated: bool
    preprocessor: MultimodalPreprocessor
    regime_transform: ArrayTransform
    target_mean: float
    target_std: float


def make_research_split(dataset: ResearchDataset, config: ResearchConfig) -> ResearchSplit:
    """Create purged splits whose labels mature before each boundary."""
    accepted = pd.to_datetime(dataset.events["accepted_at"], utc=True)
    horizon = pd.to_datetime(dataset.events["horizon_at"], utc=True)
    finite_target = np.isfinite(dataset.target)
    development_end = (
        pd.Timestamp(config.evaluation.development_end, tz="UTC")
        + pd.Timedelta(days=1)
        - pd.Timedelta(microseconds=1)
    )
    validation_start = pd.Timestamp(config.evaluation.validation_start, tz="UTC")
    validation_end = (
        pd.Timestamp(config.evaluation.validation_end, tz="UTC")
        + pd.Timedelta(days=1)
        - pd.Timedelta(microseconds=1)
    )
    test_start = pd.Timestamp(config.evaluation.test_start, tz="UTC")
    as_of_end = (
        pd.Timestamp(dataset.as_of, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    )
    development = np.flatnonzero(
        ((accepted <= development_end) & (horizon <= development_end)).to_numpy() & finite_target
    )
    validation = np.flatnonzero(
        (
            (accepted >= validation_start)
            & (accepted <= validation_end)
            & (horizon <= validation_end)
        ).to_numpy()
        & finite_target
    )
    test = np.flatnonzero(
        ((accepted >= test_start) & (horizon <= as_of_end)).to_numpy() & finite_target
    )
    minimum = config.evaluation.minimum_split_events
    counts = {
        "development": len(development),
        "validation": len(validation),
        "test": len(test),
    }
    insufficient = {name: count for name, count in counts.items() if count < minimum}
    if insufficient:
        raise ValueError(
            f"Authenticated study needs at least {minimum} matured events per split; "
            f"observed {counts}"
        )
    return ResearchSplit(development, validation, test)


def make_moe_specifications(
    config: ResearchConfig,
    maximum_candidates: int | None = None,
) -> list[MoESpecification]:
    """Build a deterministic, de-duplicated hyperparameter grid."""
    raw = [
        MoESpecification(hidden_dim, dropout, gate_strength)
        for hidden_dim in config.model.candidate_hidden_dims
        for dropout in config.model.candidate_dropouts
        for gate_strength in config.model.candidate_gate_strengths
    ]
    configured = MoESpecification(
        config.model.hidden_dim,
        config.model.dropout,
        config.model.gate_strength,
    )
    raw.append(configured)
    specifications = list(
        {(item.hidden_dim, item.dropout, item.gate_strength): item for item in raw}.values()
    )
    if maximum_candidates is not None:
        if maximum_candidates < 1:
            raise ValueError("maximum_candidates must be positive")
        specifications = specifications[:maximum_candidates]
    return specifications


def run_authenticated_experiment(
    dataset: ResearchDataset,
    *,
    config: ResearchConfig | None = None,
    evaluate_locked_test: bool = False,
    max_epochs: int | None = None,
    maximum_candidates: int | None = None,
    device: str | None = None,
) -> AuthenticatedStudyResult:
    """Select on validation and open the locked test only when explicitly requested."""
    if torch is None:
        raise RuntimeError("Install research dependencies with `uv sync --extra research`")
    config = config or ResearchConfig()
    split = make_research_split(dataset, config)
    preprocessor = MultimodalPreprocessor()
    preprocessor.fit(
        {name: values[split.development] for name, values in dataset.modalities.items()}
    )
    transformed, _ = preprocessor.transform(dataset.modalities)
    regime_transform = ArrayTransform.fit(dataset.regime[split.development])
    transformed_regime = regime_transform.transform(dataset.regime)
    missing_mask = dataset.events[
        ["text_missing", "fundamental_missing", "market_missing"]
    ].to_numpy(dtype=np.float32)
    target_mean = float(np.mean(dataset.target[split.development]))
    target_std = float(np.std(dataset.target[split.development])) or 1.0
    feature_values = {
        **transformed,
        "regime": transformed_regime,
        "missing_mask": missing_mask,
    }
    train_values = _slice_values(feature_values, split.development)
    validation_values = _slice_values(feature_values, split.validation)
    train_values["target"] = (
        (dataset.target[split.development] - target_mean) / target_std
    ).astype(np.float32)
    validation_values["target"] = (
        (dataset.target[split.validation] - target_mean) / target_std
    ).astype(np.float32)
    test_values = _slice_values(feature_values, split.test) if evaluate_locked_test else None

    specifications = make_moe_specifications(config, maximum_candidates)
    candidate_results: list[MoECandidateResult] = []
    training_epochs = max_epochs or config.model.max_epochs
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
            train_values,
            validation_values,
            learning_rate=config.model.learning_rate,
            weight_decay=config.model.weight_decay,
            entropy_regularization=config.model.entropy_regularization,
            expert_auxiliary_weight=config.model.expert_auxiliary_weight,
            correlation_regularization=config.model.correlation_regularization,
            batch_size=config.model.batch_size,
            max_epochs=training_epochs,
            patience=config.model.patience,
            seed=config.project.random_seed,
            device=device,
        )
        prediction = predict_moe(training.model, validation_values, device=device)
        validation_scores = prediction.scores * target_std + target_mean
        metrics = predictive_metrics(dataset.target[split.validation], validation_scores)
        candidate_results.append(
            MoECandidateResult(
                name=specification.name,
                hidden_dim=specification.hidden_dim,
                dropout=specification.dropout,
                gate_strength=specification.gate_strength,
                validation_metrics=metrics,
                validation_scores=validation_scores,
                training=training,
            )
        )
    selected = min(
        candidate_results,
        key=lambda item: (
            item.validation_metrics["rmse"],
            -_finite_or(item.validation_metrics.get("rank_ic"), -math.inf),
        ),
    )

    prediction_indices = (
        np.arange(len(dataset.events))
        if evaluate_locked_test
        else np.concatenate([split.development, split.validation])
    )
    unlocked_prediction = predict_moe(
        selected.training.model,
        _slice_values(feature_values, prediction_indices),
        device=device,
    )
    scores = np.full(len(dataset.events), np.nan, dtype=np.float32)
    scores[prediction_indices] = unlocked_prediction.scores * target_std + target_mean
    validation_reference = np.sort(scores[split.validation])
    ranks = np.full(len(dataset.events), np.nan, dtype=np.float32)
    ranks[prediction_indices] = np.searchsorted(
        validation_reference, scores[prediction_indices], side="right"
    ) / len(validation_reference)
    expert_weights = np.full(
        (len(dataset.events), unlocked_prediction.expert_weights.shape[1]),
        np.nan,
        dtype=np.float32,
    )
    expert_predictions = np.full(
        (len(dataset.events), unlocked_prediction.expert_predictions.shape[1]),
        np.nan,
        dtype=np.float32,
    )
    expert_weights[prediction_indices] = unlocked_prediction.expert_weights
    expert_predictions[prediction_indices] = unlocked_prediction.expert_predictions * target_std
    comparisons_train = {**train_values, "target": dataset.target[split.development]}
    comparisons_validation = {
        **validation_values,
        "target": dataset.target[split.validation],
    }
    comparisons = train_comparisons(
        comparisons_train,
        comparisons_validation,
        dataset.target[split.validation],
        test_values,
        seed=config.project.random_seed,
    )

    locked_test_metrics: dict[str, float] | None = None
    locked_comparison_metrics: dict[str, dict[str, float]] = {}
    portfolio_scenarios: list[dict[str, float | int | None]] = []
    equity_curves: dict[str, list[dict[str, Any]]] = {}
    backtest: BacktestResult | None = None
    if evaluate_locked_test:
        locked_test_metrics = predictive_metrics(dataset.target[split.test], scores[split.test])
        for comparison in comparisons:
            if comparison.test_predictions is not None:
                locked_comparison_metrics[comparison.name] = predictive_metrics(
                    dataset.target[split.test], comparison.test_predictions
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
        signals["score"] = scores[split.test]
        test_start = pd.Timestamp(config.evaluation.test_start)
        daily_returns = dataset.daily_returns.loc[
            pd.to_datetime(dataset.daily_returns["date"]).dt.tz_localize(None) >= test_start
        ]
        backtest = run_event_backtest(signals, daily_returns, config.portfolio)
        portfolio_scenarios, equity_curves = portfolio_outputs(
            backtest,
            config,
            seed=config.project.random_seed,
        )

    run_id = (
        f"{dataset.dataset_id}-"
        f"{selected.hidden_dim}-{selected.dropout:.2f}-"
        f"{selected.gate_strength:.2f}-"
        f"{config.project.random_seed}"
    )
    return AuthenticatedStudyResult(
        run_id=run_id,
        dataset_id=dataset.dataset_id,
        split=split,
        selected_candidate=selected,
        candidates=candidate_results,
        comparisons=comparisons,
        scores=scores,
        ranks=ranks,
        expert_weights=expert_weights,
        expert_predictions=expert_predictions,
        validation_metrics=selected.validation_metrics,
        locked_test_metrics=locked_test_metrics,
        locked_comparison_metrics=locked_comparison_metrics,
        portfolio_scenarios=portfolio_scenarios,
        equity_curves=equity_curves,
        backtest=backtest,
        locked_test_evaluated=evaluate_locked_test,
        preprocessor=preprocessor,
        regime_transform=regime_transform,
        target_mean=target_mean,
        target_std=target_std,
    )


def save_study_artifacts(
    result: AuthenticatedStudyResult,
    output_root: str | Path,
    *,
    config: ResearchConfig,
    force: bool = False,
) -> Path:
    """Persist selection and, if opened, immutable locked-test artifacts."""
    if torch is None:
        raise RuntimeError("Install research dependencies with `uv sync --extra research`")
    output = Path(output_root) / result.dataset_id
    output.mkdir(parents=True, exist_ok=True)
    selection_path = output / "selection.json"
    locked_path = output / "locked-test.json"
    if result.locked_test_evaluated and locked_path.exists() and not force:
        raise FileExistsError(
            f"Locked test already exists at {locked_path}; refusing to overwrite without force"
        )
    selection_payload = {
        "run_id": result.run_id,
        "dataset_id": result.dataset_id,
        "configuration": config.model_dump(mode="json"),
        "split_counts": {
            "development": len(result.split.development),
            "validation": len(result.split.validation),
            "test": len(result.split.test),
        },
        "selected": _candidate_record(result.selected_candidate, selected=True),
        "candidates": [
            _candidate_record(candidate, candidate.name == result.selected_candidate.name)
            for candidate in result.candidates
        ],
        "comparisons": [
            {
                "name": item.name,
                "family": item.family,
                **_finite_mapping(item.validation_metrics),
            }
            for item in result.comparisons
        ],
    }
    selection_payload["selection_hash"] = _payload_hash(selection_payload)
    _write_json(selection_path, selection_payload)
    model_path = output / "selected-model.pt"
    torch.save(
        {
            "run_id": result.run_id,
            "state_dict": result.selected_candidate.training.model.state_dict(),
            "model_dimensions": result.selected_candidate.training.model.export_config(),
            "hidden_dim": result.selected_candidate.hidden_dim,
            "expert_dim": config.model.expert_dim,
            "dropout": result.selected_candidate.dropout,
            "gate_strength": result.selected_candidate.gate_strength,
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
        },
        model_path,
    )
    if result.locked_test_evaluated:
        locked_payload = {
            "run_id": result.run_id,
            "dataset_id": result.dataset_id,
            "selection_hash": selection_payload["selection_hash"],
            "primary_model": _finite_mapping(result.locked_test_metrics or {}),
            "comparisons": {
                name: _finite_mapping(metrics)
                for name, metrics in result.locked_comparison_metrics.items()
            },
            "portfolio_scenarios": result.portfolio_scenarios,
        }
        locked_payload["locked_test_hash"] = _payload_hash(locked_payload)
        _write_json(locked_path, locked_payload)
    return output


def _slice_values(values: dict[str, np.ndarray], indices: np.ndarray) -> dict[str, np.ndarray]:
    return {name: array[indices] for name, array in values.items()}


def portfolio_outputs(
    backtest: BacktestResult,
    config: ResearchConfig,
    *,
    seed: int,
) -> tuple[list[dict[str, float | int | None]], dict[str, list[dict[str, Any]]]]:
    scenarios: list[dict[str, float | int | None]] = []
    curves: dict[str, list[dict[str, Any]]] = {}
    for cost_bps in (10, 25, 50):
        daily = backtest.daily.copy()
        daily["net_return"] = (
            daily["gross_return"] - daily["turnover"] * cost_bps / 10_000 - daily["borrow_cost"]
        )
        daily["equity"] = (1 + daily["net_return"]).cumprod()
        metrics = performance_metrics(daily)
        low, high = block_bootstrap_sharpe_interval(
            daily["net_return"].to_numpy(),
            samples=config.evaluation.bootstrap_samples,
            seed=seed,
        )
        metrics["sharpe_ci_low"] = low
        metrics["sharpe_ci_high"] = high
        scenarios.append({"cost_bps": cost_bps, **_finite_mapping(metrics)})
        running_peak = daily["equity"].cummax()
        drawdown = daily["equity"] / running_peak - 1
        curves[f"cost_{cost_bps}bps"] = [
            {
                "date": pd.Timestamp(day).date().isoformat(),
                "equity": round(float(equity), 8),
                "drawdown": round(float(point_drawdown), 8),
                "turnover": round(float(turnover), 8),
            }
            for day, equity, point_drawdown, turnover in zip(
                daily["date"], daily["equity"], drawdown, daily["turnover"], strict=True
            )
        ]
    return scenarios, curves


def _candidate_record(candidate: MoECandidateResult, selected: bool) -> dict[str, Any]:
    return {
        "name": candidate.name,
        "family": "multimodal",
        "hidden_dim": candidate.hidden_dim,
        "dropout": candidate.dropout,
        "gate_strength": candidate.gate_strength,
        "best_epoch": candidate.training.best_epoch,
        "selected": selected,
        **_finite_mapping(candidate.validation_metrics),
    }


def _finite_mapping(values: dict[str, float]) -> dict[str, float | None]:
    return {
        key: float(value) if math.isfinite(float(value)) else None for key, value in values.items()
    }


def _finite_or(value: float | None, fallback: float) -> float:
    return float(value) if value is not None and math.isfinite(value) else fallback


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    temporary.replace(path)
