"""Read-only reproduction of frozen preprocessing and saved locked-test scores.

Run from the repository root with research dependencies installed. No training,
registry access, artifact writes, or model selection is performed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from edgar_moe.data.storage import sha256_file
from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.forward.config import FrozenModelSpec
from edgar_moe.forward.inference import FrozenPredictor
from edgar_moe.modeling.frozen import (
    _make_frozen_split,
    _prepare_frozen_values,
    verify_frozen_selection,
)
from edgar_moe.modeling.train import predict_moe
from edgar_moe.settings import ResearchConfig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/forward.yaml"))
    parser.add_argument("--selection", type=Path, required=True)
    args = parser.parse_args()
    spec = FrozenModelSpec.from_yaml(args.config)
    locked = spec.verify_locked_evidence()
    selection = verify_frozen_selection(
        args.selection,
        dataset_id=spec.training_dataset_id,
        confirmation_hash=spec.selection_hash,
    )
    predictor = FrozenPredictor.load(
        spec.model_path,
        expected_sha256=spec.artifact_sha256,
        expected_selection_hash=spec.selection_hash,
    )
    dataset = ResearchDataset.load(spec.training_dataset_dir)
    if dataset.dataset_id != spec.training_dataset_id:
        raise ValueError("Training dataset identity mismatch")
    split = _make_frozen_split(dataset, ResearchConfig.model_validate(selection["configuration"]))
    _, test, prep, regime, mean, std = _prepare_frozen_values(dataset, split)
    np.testing.assert_equal([predictor.target_mean, predictor.target_std], [mean, std])
    if predictor.champion_parameters != locked["champion_parameters"]:
        raise ValueError("Checkpoint and locked result disagree on champion parameters")
    for name, fresh in prep.transforms.items():
        saved = predictor.preprocessor.transforms[name]
        np.testing.assert_allclose(saved.medians, fresh.medians, rtol=0, atol=0)
        np.testing.assert_allclose(saved.scaler.mean_, fresh.scaler.mean_, rtol=0, atol=0)
        np.testing.assert_allclose(saved.scaler.scale_, fresh.scaler.scale_, rtol=0, atol=0)
    np.testing.assert_allclose(predictor.regime_transform.medians, regime.medians, rtol=0, atol=0)
    np.testing.assert_allclose(
        predictor.regime_transform.scaler.mean_,
        regime.scaler.mean_,
        rtol=0,
        atol=0,
    )
    np.testing.assert_allclose(
        predictor.regime_transform.scaler.scale_,
        regime.scaler.scale_,
        rtol=0,
        atol=0,
    )
    prediction = predict_moe(predictor.model, test, device="cpu")
    neural = prediction.scores * predictor.target_std + predictor.target_mean
    if predictor.champion_family != "anchored_multimodal":
        raise ValueError("This audit expects the frozen anchored champion")
    if predictor.fundamental_coef is None or predictor.fundamental_intercept is None:
        raise ValueError("Fundamental component is missing")
    anchor = test["fundamental"] @ predictor.fundamental_coef + predictor.fundamental_intercept
    weights = predictor.champion_parameters
    scores = weights["fundamental_anchor_weight"] * anchor + weights["moe_residual_weight"] * neural
    score_path = spec.model_path.parent / "locked-test-scores.npz"
    if sha256_file(score_path) != locked["scores_sha256"]:
        raise ValueError("Locked score archive hash mismatch")
    with np.load(score_path, allow_pickle=False) as archive:
        np.testing.assert_array_equal(archive["test_indices"], split.test)
        error = float(np.max(np.abs(scores - archive["scores"])))
        np.testing.assert_allclose(scores, archive["scores"], rtol=1e-5, atol=1e-7)
    print(
        json.dumps(
            {
                "status": "passed",
                "artifact_sha256": spec.artifact_sha256,
                "train_events": len(split.train),
                "test_events": len(split.test),
                "target_mean": mean,
                "target_std": std,
                "champion_parameters": weights,
                "max_saved_score_error": error,
                "scope": "Artifact reproduction, not a new test or model selection",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
