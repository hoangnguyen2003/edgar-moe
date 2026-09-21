from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import ElasticNet
from sklearn.metrics import mean_squared_error


def fit_on_standardized_target(
    estimator: ElasticNet, features: np.ndarray, target: np.ndarray
) -> ElasticNet:
    """Fit an Elastic Net whose penalty is defined on a unit-variance target.

    Raw 20-session returns have a standard deviation near 0.08, so a fixed alpha
    silently zeroes weak coefficients (the frozen v1 Text-Only baseline kept 0 of
    776). The coefficients are rescaled afterwards, so ``predict`` and persisted
    weights remain in return units.
    """
    values = np.asarray(target, dtype=np.float64)
    mean = float(values.mean())
    scale = float(values.std()) or 1.0
    estimator.fit(features, (values - mean) / scale)
    estimator.coef_ = np.asarray(estimator.coef_) * scale
    estimator.intercept_ = float(estimator.intercept_) * scale + mean
    return estimator


@dataclass(frozen=True)
class BaselineResult:
    name: str
    validation_rmse: float
    predictions: np.ndarray


def train_baselines(
    train_features: np.ndarray,
    train_target: np.ndarray,
    validation_features: np.ndarray,
    validation_target: np.ndarray,
    seed: int = 42,
) -> list[BaselineResult]:
    models = {
        "Elastic Net": ElasticNet(alpha=0.02, l1_ratio=0.2, max_iter=10_000, random_state=seed),
        "Gradient Boosting": HistGradientBoostingRegressor(
            max_iter=180,
            learning_rate=0.04,
            max_leaf_nodes=15,
            l2_regularization=1.0,
            random_state=seed,
        ),
    }
    results: list[BaselineResult] = []
    for name, model in models.items():
        if isinstance(model, ElasticNet):
            fit_on_standardized_target(model, train_features, train_target)
        else:
            model.fit(train_features, train_target)
        predictions = model.predict(validation_features)
        results.append(
            BaselineResult(
                name=name,
                validation_rmse=float(mean_squared_error(validation_target, predictions) ** 0.5),
                predictions=predictions,
            )
        )
    return results
