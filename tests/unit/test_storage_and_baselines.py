import numpy as np
import polars as pl

from edgar_moe.data.storage import ResearchStore, stable_json_hash
from edgar_moe.modeling.baselines import train_baselines


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
