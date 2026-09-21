import numpy as np
import polars as pl
from scipy.stats import spearmanr
from sklearn.linear_model import ElasticNet

from edgar_moe.data.storage import ResearchStore, stable_json_hash
from edgar_moe.modeling.baselines import fit_on_standardized_target, train_baselines


def test_research_store_catalogs_versioned_parquet(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    path = store.write_parquet("features", pl.DataFrame({"event_id": ["a", "b"], "x": [1, 2]}))

    assert path.exists()
    assert store.catalog()[0]["row_count"] == 2
    result = store.query(f"SELECT count(*) AS rows FROM read_parquet('{path}')")
    assert result["rows"].item() == 2
    assert stable_json_hash({"a": 1, "b": 2}) == stable_json_hash({"b": 2, "a": 1})


def test_baselines_return_validation_predictions() -> None:
    generator = np.random.default_rng(7)
    features = generator.normal(size=(100, 4))
    target = 0.5 * features[:, 0] - 0.2 * features[:, 1] + generator.normal(0, 0.02, 100)
    results = train_baselines(features[:80], target[:80], features[80:], target[80:], seed=7)

    assert {result.name for result in results} == {"Elastic Net", "Gradient Boosting"}
    assert all(result.predictions.shape == (20,) for result in results)
    assert all(result.validation_rmse < 0.5 for result in results)


def _weak_signal(seed: int = 11) -> tuple[np.ndarray, np.ndarray]:
    # Twenty-session abnormal returns: sigma near 0.08 and true effects just
    # below the raw-scale L1 threshold (alpha * l1_ratio = 0.004), like v1's
    # individually weak text features.
    generator = np.random.default_rng(seed)
    features = generator.normal(size=(20_000, 20))
    target = 0.002 * features[:, 0] - 0.0015 * features[:, 1] + generator.normal(0, 0.08, 20_000)
    return features, target


def test_fixed_alpha_on_raw_returns_zeroes_every_coefficient() -> None:
    features, target = _weak_signal()
    raw = ElasticNet(alpha=0.02, l1_ratio=0.2, max_iter=10_000).fit(features, target)

    # This reproduces the v1 comparison defect (Text-Only kept 0 of 776 inputs).
    assert not np.any(raw.coef_)


def test_standardized_target_elastic_net_keeps_signal_in_return_units() -> None:
    features, target = _weak_signal()
    model = fit_on_standardized_target(
        ElasticNet(alpha=0.02, l1_ratio=0.2, max_iter=10_000), features, target
    )
    predictions = model.predict(features)

    assert np.count_nonzero(model.coef_[:2]) == 2
    assert model.coef_[0] > 0 > model.coef_[1]
    assert spearmanr(predictions, target).statistic > 0.015
    # Persisted weights must reproduce predict() in return units.
    np.testing.assert_allclose(features @ model.coef_ + model.intercept_, predictions)
    assert abs(float(predictions.mean()) - float(target.mean())) < 1e-3
