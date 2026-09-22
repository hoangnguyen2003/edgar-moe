from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "compare_frozen_runtime_reports.py"
_SPEC = importlib.util.spec_from_file_location("compare_frozen_runtime_reports", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _report(*, torch_version: str = "2.13.0") -> dict[str, object]:
    return {
        "status": "passed",
        "artifact_sha256": "a" * 64,
        "train_events": 4003,
        "test_events": 3,
        "target_mean": -0.002,
        "target_std": 0.076,
        "champion_parameters": {"hidden_dim": 64, "dropout": 0.1},
        "max_saved_score_error": 7.5e-10,
        "runtime": {
            "torch": torch_version,
            "transformers": "4.57.6",
            "numpy": "2.5.1",
            "scikit-learn": "1.9.0",
            "pandas": "2.3.3",
        },
    }


def _write_inputs(root: Path, *, candidate_scores: list[float] | None = None) -> tuple[Path, ...]:
    baseline = root / "baseline.json"
    candidate = root / "candidate.json"
    scores = np.asarray([0.1, -0.2, 0.3], dtype=np.float32)
    candidate_values = np.asarray(candidate_scores or scores, dtype=np.float32)
    baseline_scores = root / "baseline-scores.npz"
    candidate_scores_path = root / "candidate-scores.npz"
    np.savez_compressed(baseline_scores, test_indices=np.arange(3), scores=scores)
    np.savez_compressed(candidate_scores_path, test_indices=np.arange(3), scores=candidate_values)
    baseline_report = _report()
    candidate_report = _report(torch_version="2.14.0")
    baseline_report.update(
        scores_shape=list(scores.shape),
        scores_sha256=hashlib.sha256(scores.tobytes()).hexdigest(),
        scores_archive_sha256=hashlib.sha256(baseline_scores.read_bytes()).hexdigest(),
    )
    candidate_report.update(
        scores_shape=list(candidate_values.shape),
        scores_sha256=hashlib.sha256(candidate_values.tobytes()).hexdigest(),
        scores_archive_sha256=hashlib.sha256(candidate_scores_path.read_bytes()).hexdigest(),
    )
    baseline.write_text(json.dumps(baseline_report), encoding="utf-8")
    candidate.write_text(json.dumps(candidate_report), encoding="utf-8")
    return baseline, candidate, baseline_scores, candidate_scores_path


def test_matching_reproduction_reports_pass(tmp_path: Path) -> None:
    inputs = _write_inputs(tmp_path)

    result = _MODULE.compare_reports(*inputs)

    assert result["status"] == "passed"
    assert result["max_abs_score_delta"] == 0.0
    assert result["candidate_runtime"]["torch"] == "2.14.0"


def test_score_drift_fails_closed(tmp_path: Path) -> None:
    inputs = _write_inputs(tmp_path, candidate_scores=[0.1, -0.2, 0.31])

    result = _MODULE.compare_reports(*inputs, max_score_delta=1e-4)

    assert result["status"] == "failed"
    assert "reproduced scores exceed the configured compatibility tolerance" in result["errors"]


def test_score_metadata_tampering_fails_closed(tmp_path: Path) -> None:
    inputs = _write_inputs(tmp_path)
    np.savez_compressed(
        inputs[3],
        test_indices=np.arange(3),
        scores=np.asarray([0.1, -0.2, 0.300001], dtype=np.float32),
    )

    result = _MODULE.compare_reports(*inputs)

    assert result["status"] == "failed"
    assert "candidate report archive digest does not match its score archive" in result["errors"]


def test_identity_drift_fails_closed(tmp_path: Path) -> None:
    inputs = _write_inputs(tmp_path)
    candidate = json.loads(inputs[1].read_text(encoding="utf-8"))
    candidate["artifact_sha256"] = "b" * 64
    inputs[1].write_text(json.dumps(candidate), encoding="utf-8")

    result = _MODULE.compare_reports(*inputs)

    assert result["status"] == "failed"
    assert "frozen identity differs: artifact_sha256" in result["errors"]
