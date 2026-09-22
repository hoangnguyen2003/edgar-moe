"""Compare private frozen-v1 reproduction reports across dependency environments.

The command is deliberately offline: it reads two reports and two score archives
created by ``audit_frozen_normalization.py`` and never contacts a provider. It is
intended for reviewing a dependency upgrade such as torch before that upgrade is
allowed to alter the frozen forward runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

_IDENTITY_FIELDS = (
    "artifact_sha256",
    "train_events",
    "test_events",
    "champion_parameters",
)
_REQUIRED_RUNTIME_VERSIONS = ("torch", "transformers", "numpy", "scikit-learn", "pandas")


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"report must be a JSON object: {path}")
    return payload


def _load_scores(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with np.load(path, allow_pickle=False) as archive:
        if "test_indices" not in archive or "scores" not in archive:
            raise ValueError(f"score archive must contain test_indices and scores: {path}")
        return (
            np.asarray(archive["test_indices"]),
            np.asarray(archive["scores"], dtype=np.float32),
        )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _validate_score_metadata(
    report: dict[str, Any],
    label: str,
    path: Path,
    scores: np.ndarray,
    errors: list[str],
) -> None:
    expected_shape = report.get("scores_shape")
    if expected_shape != list(scores.shape):
        errors.append(f"{label} report score shape does not match its score archive")
    expected_scores_hash = report.get("scores_sha256")
    actual_scores_hash = _sha256_bytes(np.ascontiguousarray(scores, dtype=np.float32).tobytes())
    if expected_scores_hash != actual_scores_hash:
        errors.append(f"{label} report score digest does not match its score archive")
    expected_archive_hash = report.get("scores_archive_sha256")
    if not isinstance(expected_archive_hash, str) or expected_archive_hash != _sha256_file(path):
        errors.append(f"{label} report archive digest does not match its score archive")


def _validate_runtime(report: dict[str, Any], label: str, errors: list[str]) -> dict[str, str]:
    runtime = report.get("runtime")
    if not isinstance(runtime, dict):
        errors.append(f"{label} report is missing runtime versions")
        return {}
    versions: dict[str, str] = {}
    for package in _REQUIRED_RUNTIME_VERSIONS:
        value = runtime.get(package)
        if not isinstance(value, str) or not value.strip() or value == "unavailable":
            errors.append(f"{label} runtime version is missing: {package}")
        else:
            versions[package] = value
    return versions


def compare_reports(
    baseline_path: Path,
    candidate_path: Path,
    baseline_scores_path: Path,
    candidate_scores_path: Path,
    *,
    max_score_error: float = 1e-6,
    max_score_delta: float = 1e-6,
) -> dict[str, Any]:
    """Return a deterministic compatibility decision without contacting providers."""
    baseline = _read_object(baseline_path)
    candidate = _read_object(candidate_path)
    errors: list[str] = []
    baseline_runtime = _validate_runtime(baseline, "baseline", errors)
    candidate_runtime = _validate_runtime(candidate, "candidate", errors)
    for label, report in (("baseline", baseline), ("candidate", candidate)):
        if report.get("status") != "passed":
            errors.append(f"{label} report status is not passed")
        observed_error = report.get("max_saved_score_error")
        if not isinstance(observed_error, (int, float)) or not math.isfinite(float(observed_error)):
            errors.append(f"{label} max_saved_score_error is not finite")
        elif float(observed_error) > max_score_error:
            errors.append(f"{label} max_saved_score_error exceeds tolerance")

    for field in _IDENTITY_FIELDS:
        if baseline.get(field) != candidate.get(field):
            errors.append(f"frozen identity differs: {field}")
    for field in ("target_mean", "target_std"):
        left = baseline.get(field)
        right = candidate.get(field)
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            errors.append(f"frozen identity is missing numeric field: {field}")
        elif not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-7):
            errors.append(f"frozen identity differs: {field}")

    max_abs_score_delta: float | None = None
    try:
        baseline_indices, baseline_scores = _load_scores(baseline_scores_path)
        candidate_indices, candidate_scores = _load_scores(candidate_scores_path)
        _validate_score_metadata(
            baseline,
            "baseline",
            baseline_scores_path,
            baseline_scores,
            errors,
        )
        _validate_score_metadata(
            candidate,
            "candidate",
            candidate_scores_path,
            candidate_scores,
            errors,
        )
        if not np.array_equal(baseline_indices, candidate_indices):
            errors.append("score archives contain different test indices")
        if baseline_scores.shape != candidate_scores.shape:
            errors.append("score archives contain different score shapes")
        else:
            max_abs_score_delta = float(np.max(np.abs(baseline_scores - candidate_scores)))
            if not math.isfinite(max_abs_score_delta) or max_abs_score_delta > max_score_delta:
                errors.append("reproduced scores exceed the configured compatibility tolerance")
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(f"score archive comparison failed: {error}")

    return {
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "baseline_runtime": baseline_runtime,
        "candidate_runtime": candidate_runtime,
        "max_score_error": max_score_error,
        "max_score_delta": max_score_delta,
        "max_abs_score_delta": max_abs_score_delta,
        "identity": {
            "artifact_sha256": baseline.get("artifact_sha256"),
            "test_events": baseline.get("test_events"),
            "champion_parameters": baseline.get("champion_parameters"),
        },
        "scope": "Dependency reproduction comparison; not a new test or model selection",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline-scores", type=Path, required=True)
    parser.add_argument("--candidate-scores", type=Path, required=True)
    parser.add_argument("--max-score-error", type=float, default=1e-6)
    parser.add_argument("--max-score-delta", type=float, default=1e-6)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.max_score_error < 0 or args.max_score_delta < 0:
        parser.error("compatibility tolerances must be non-negative")
    try:
        report = compare_reports(
            args.baseline,
            args.candidate,
            args.baseline_scores,
            args.candidate_scores,
            max_score_error=args.max_score_error,
            max_score_delta=args.max_score_delta,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
