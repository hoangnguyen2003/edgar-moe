from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import ElasticNet
from sklearn.neural_network import MLPRegressor

from edgar_moe.backtest.metrics import predictive_metrics
from edgar_moe.modeling.baselines import fit_on_standardized_target


@dataclass(frozen=True)
class ComparisonResult:
    name: str
    family: str
    validation_predictions: np.ndarray
    test_predictions: np.ndarray | None
    validation_metrics: dict[str, float]
    model: Any | None = None


def train_comparisons(
    train: dict[str, np.ndarray],
    validation: dict[str, np.ndarray],
    validation_target: np.ndarray,
    test: dict[str, np.ndarray] | None = None,
    *,
    seed: int = 42,
) -> list[ComparisonResult]:
    """Fit preregistered linear, tree, modality, and static-fusion comparisons."""
    feature_sets = {
        "all": ("text", "fundamental", "market", "regime"),
        "tabular": ("fundamental", "market", "regime"),
        "text": ("text",),
        "fundamental": ("fundamental",),
        "market": ("market",),
    }
    specifications: list[tuple[str, str, tuple[str, ...], Any]] = [
        (
            "Elastic Net",
            "linear",
            feature_sets["all"],
            ElasticNet(
                alpha=0.02,
                l1_ratio=0.2,
                max_iter=10_000,
                random_state=seed,
            ),
        ),
        (
            "Gradient-Boosted Tabular",
            "tree",
            feature_sets["tabular"],
            HistGradientBoostingRegressor(
                max_iter=180,
                learning_rate=0.04,
                max_leaf_nodes=15,
                l2_regularization=1.0,
                random_state=seed,
            ),
        ),
        (
            "Text-Only Expert",
            "single_modality",
            feature_sets["text"],
            ElasticNet(
                alpha=0.02,
                l1_ratio=0.2,
                max_iter=10_000,
                random_state=seed,
            ),
        ),
        (
            "Fundamental-Only Expert",
            "single_modality",
            feature_sets["fundamental"],
            ElasticNet(
                alpha=0.02,
                l1_ratio=0.2,
                max_iter=10_000,
                random_state=seed,
            ),
        ),
        (
            "Market-Only Expert",
            "single_modality",
            feature_sets["market"],
            ElasticNet(
                alpha=0.02,
                l1_ratio=0.2,
                max_iter=10_000,
                random_state=seed,
            ),
        ),
        (
            "Static Early-Fusion MLP",
            "static_multimodal",
            feature_sets["all"],
            MLPRegressor(
                hidden_layer_sizes=(64, 32),
                activation="relu",
                alpha=1e-4,
                batch_size=256,
                learning_rate_init=1e-3,
                max_iter=250,
                early_stopping=True,
                validation_fraction=0.15,
                n_iter_no_change=12,
                random_state=seed,
            ),
        ),
    ]
    fitted: list[ComparisonResult] = []
    modality_validation: list[np.ndarray] = []
    modality_test: list[np.ndarray] = []
    for name, family, columns, estimator in specifications:
        train_values = _stack(train, columns)
        validation_values = _stack(validation, columns)
        if isinstance(estimator, ElasticNet):
            # Frozen v1 fit these on raw returns; see fit_on_standardized_target.
            fit_on_standardized_target(estimator, train_values, train["target"])
        else:
            estimator.fit(train_values, train["target"])
        validation_prediction = np.asarray(estimator.predict(validation_values), dtype=np.float64)
        test_prediction = (
            np.asarray(estimator.predict(_stack(test, columns)), dtype=np.float64)
            if test is not None
            else None
        )
        fitted.append(
            ComparisonResult(
                name=name,
                family=family,
                validation_predictions=validation_prediction,
                test_predictions=test_prediction,
                validation_metrics=predictive_metrics(validation_target, validation_prediction),
                model=estimator,
            )
        )
        if family == "single_modality":
            modality_validation.append(validation_prediction)
            if test_prediction is not None:
                modality_test.append(test_prediction)

    equal_validation = np.mean(np.vstack(modality_validation), axis=0)
    equal_test = np.mean(np.vstack(modality_test), axis=0) if modality_test else None
    fitted.append(
        ComparisonResult(
            name="Equal-Weight Expert Ensemble",
            family="static_multimodal",
            validation_predictions=equal_validation,
            test_predictions=equal_test,
            validation_metrics=predictive_metrics(validation_target, equal_validation),
        )
    )
    return fitted


def _stack(values: dict[str, np.ndarray], columns: tuple[str, ...]) -> np.ndarray:
    return np.column_stack([values[column] for column in columns])
