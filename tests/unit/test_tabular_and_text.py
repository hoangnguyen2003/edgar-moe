import numpy as np
import pandas as pd
import pytest

from edgar_moe.features.tabular import build_fundamental_ratios, cross_sectional_winsorize
from edgar_moe.features.text import HashingTextEmbedder, filing_change_features


def test_fundamental_ratios_are_missing_aware() -> None:
    ratios = build_fundamental_ratios(
        pd.DataFrame(
            {
                "Assets": [100.0, 0.0],
                "Liabilities": [40.0, 10.0],
                "StockholdersEquity": [60.0, np.nan],
                "Revenues": [50.0, 20.0],
                "NetIncomeLoss": [5.0, -2.0],
            }
        )
    )
    assert ratios.loc[0, "debt_to_assets"] == pytest.approx(0.4)
    assert np.isnan(ratios.loc[1, "debt_to_assets"])
    assert ratios.loc[1, "debt_to_assets__missing"] == 1


def test_winsorization_does_not_mix_event_dates() -> None:
    frame = pd.DataFrame(
        {
            "event_date": ["2026-01-01"] * 4 + ["2026-02-01"] * 4,
            "value": [1.0, 2.0, 3.0, 1000.0, 100.0, 200.0, 300.0, 100_000.0],
        }
    )
    result = cross_sectional_winsorize(frame, ["value"], lower=0.0, upper=0.75)
    assert result.loc[3, "value"] < 1000
    assert result.loc[7, "value"] > result.loc[3, "value"] * 10


def test_hashing_text_features_are_deterministic_and_comparable() -> None:
    embedder = HashingTextEmbedder(dimensions=16)
    current = embedder.encode("Strong profitable growth opportunity with uncertain risk.")
    repeated = embedder.encode("Strong profitable growth opportunity with uncertain risk.")
    previous = embedder.encode("Weak decline and adverse loss risk.")

    assert current.token_count == 7
    assert np.allclose(current.embedding, repeated.embedding)
    assert current.sentiment[-1] > current.sentiment[0]
    changes = filing_change_features(current, previous)
    assert changes["has_prior_filing"] == 1
    assert changes["embedding_cosine_change"] > 0
    assert filing_change_features(current, None)["has_prior_filing"] == 0
