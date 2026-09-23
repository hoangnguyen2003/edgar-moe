"""Reproduce conditional rank-IC intervals from the immutable v1 score archives.

This is a read-only companion analysis. It never retrains, selects a model, or
rewrites the frozen result. Run from the repository root with the research
dataset and score archives present locally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import cast

import numpy as np
import polars as pl
from scipy import stats

DATASET_ID = "research-2026-07-31-3553e7ad78dd"
DEFAULT_SEED = 20260923
DEFAULT_SAMPLES = 5000
DEFAULT_BLOCK_MONTHS = 2


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rank_ic(target: np.ndarray, score: np.ndarray) -> float:
    if len(target) != len(score) or len(target) < 3:
        raise ValueError("rank IC needs at least three aligned pairs")
    if not np.isfinite(target).all() or not np.isfinite(score).all():
        raise ValueError("rank IC pairs must be finite")
    result = float(stats.spearmanr(target, score).statistic)
    if not math.isfinite(result):
        raise ValueError("rank IC is undefined for constant input")
    return result


def month_blocks(months: np.ndarray) -> list[np.ndarray]:
    """Keep all filings in a calendar month together, including empty months."""
    periods = np.asarray(months, dtype="datetime64[M]")
    if periods.ndim != 1 or not len(periods) or np.isnat(periods).any():
        raise ValueError("months must be a nonempty, valid one-dimensional array")
    first, last = periods.min(), periods.max()
    calendar = np.arange(first, last + np.timedelta64(1, "M"), dtype="datetime64[M]")
    return [np.flatnonzero(periods == month) for month in calendar]


def resample_month_indices(
    blocks: list[np.ndarray], *, block_months: int, rng: np.random.Generator
) -> np.ndarray:
    """Overlapping moving blocks; truncate the last draw to the original span."""
    span = len(blocks)
    if not 1 <= block_months <= span:
        raise ValueError("block length must fit the calendar span")
    drawn: list[np.ndarray] = []
    remaining = span
    while remaining:
        start = int(rng.integers(0, span - block_months + 1))
        take = min(block_months, remaining)
        drawn.extend(blocks[start : start + take])
        remaining -= take
    return np.concatenate(drawn)


def interval(
    cohorts: list[tuple[np.ndarray, np.ndarray, np.ndarray]],
    *,
    block_months: int,
    samples: int,
    seed: int,
) -> dict[str, object]:
    """Bootstrap each pre-test fold separately; weight by original fold count."""
    if samples < 100:
        raise ValueError("at least 100 resamples are required")
    prepared = []
    for target, score, months in cohorts:
        target, score = np.asarray(target), np.asarray(score)
        if len(target) != len(months):
            raise ValueError("cohort arrays must align")
        point = rank_ic(target, score)
        blocks = month_blocks(months)
        if len(blocks) < block_months * 2:
            raise ValueError("too few calendar months for the requested block length")
        prepared.append((target, score, blocks, point))
    counts = np.array([len(item[0]) for item in prepared])
    point = float(np.average([item[3] for item in prepared], weights=counts))
    rng = np.random.default_rng(seed)
    replicates = np.empty(samples)
    for draw in range(samples):
        values = []
        for target, score, blocks, _ in prepared:
            indices = resample_month_indices(blocks, block_months=block_months, rng=rng)
            values.append(rank_ic(target[indices], score[indices]))
        replicates[draw] = np.average(values, weights=counts)
    low, high = np.quantile(replicates, [0.025, 0.975])
    return {
        "rank_ic": point,
        "ci_low": float(low),
        "ci_high": float(high),
        "events": int(sum(counts)),
        "calendar_months_per_cohort": [len(item[2]) for item in prepared],
        "fold_rank_ic": [item[3] for item in prepared],
    }


def _assert_same(label: str, observed: float, frozen: float) -> None:
    if not math.isclose(observed, frozen, rel_tol=0, abs_tol=1e-12):
        raise ValueError(f"{label} does not reproduce the frozen result")


def analyze(root: Path, *, block_months: int, samples: int, seed: int) -> dict[str, object]:
    dataset_root = root / "data/processed/finbert" / DATASET_ID
    walk_root = root / "data/artifacts/walk-forward" / DATASET_ID
    frozen_root = root / "data/artifacts/frozen-studies" / DATASET_ID
    manifest = json.loads((dataset_root / "manifest.json").read_text())
    selection = json.loads((walk_root / "walk-forward-selection.json").read_text())
    locked = json.loads((root / "ops/frozen/locked-test.json").read_text())
    if any(item["dataset_id"] != DATASET_ID for item in (manifest, selection, locked)):
        raise ValueError("dataset identities do not match")
    if selection["selection_hash"] != locked["selection_hash"]:
        raise ValueError("selection identity does not match the frozen result")
    if selection["champion"]["name"] != locked["champion_name"]:
        raise ValueError("champion identity does not match the frozen result")

    for asset in ("events", "features"):
        path = dataset_root / manifest["assets"][asset]
        if sha256_file(path) != manifest["hashes"][asset]:
            raise ValueError(f"authenticated dataset {asset} hash mismatch")
    oof_path = walk_root / "oof-predictions.npz"
    scores_path = frozen_root / "locked-test-scores.npz"
    if sha256_file(oof_path) != selection["oof_predictions_sha256"]:
        raise ValueError("OOF score archive hash mismatch")
    if sha256_file(scores_path) != locked["scores_sha256"]:
        raise ValueError("locked score archive hash mismatch")

    events = pl.read_parquet(dataset_root / manifest["assets"]["events"])
    with np.load(dataset_root / manifest["assets"]["features"], allow_pickle=False) as arrays:
        target = arrays["target"]
    months = events["accepted_at"].dt.strftime("%Y-%m").to_numpy().astype("datetime64[M]")
    years = events["accepted_at"].dt.year().to_numpy()
    if len(events) != len(target):
        raise ValueError("event and target arrays do not align")

    with np.load(oof_path, allow_pickle=False) as archive:
        names = archive["model_names"].tolist()
        if names.count(selection["champion"]["name"]) != 1:
            raise ValueError("selected champion is missing or ambiguous in OOF archive")
        oof_indices = archive["oof_indices"]
        oof_scores = archive["predictions"][oof_indices, names.index(locked["champion_name"])]
    with np.load(scores_path, allow_pickle=False) as archive:
        test_indices = archive["test_indices"]
        test_scores = archive["scores"]
    if len(np.unique(oof_indices)) != len(oof_indices) or len(np.unique(test_indices)) != len(
        test_indices
    ):
        raise ValueError("score archive has duplicate event indices")
    if len(test_indices) != locked["locked_test_events"]:
        raise ValueError("locked event count mismatch")
    if np.isin(oof_indices, test_indices).any():
        raise ValueError("development and locked cohorts overlap")

    development = []
    for fold in selection["folds"]:
        year = int(fold["validation_year"])
        mask = years[oof_indices] == year
        if int(mask.sum()) != fold["validation_events"]:
            raise ValueError("validation fold event count mismatch")
        cohort = (target[oof_indices[mask]], oof_scores[mask], months[oof_indices[mask]])
        _assert_same(
            fold["name"],
            rank_ic(cohort[0], cohort[1]),
            selection["champion"]["fold_metrics"][fold["name"]]["rank_ic"],
        )
        development.append(cohort)
    locked_cohort = (target[test_indices], test_scores, months[test_indices])
    _assert_same("locked test", rank_ic(*locked_cohort[:2]), locked["primary_model"]["rank_ic"])
    development_result = interval(
        development, block_months=block_months, samples=samples, seed=seed
    )
    _assert_same(
        "development weighted mean",
        cast(float, development_result["rank_ic"]),
        selection["champion"]["aggregate_metrics"]["rank_ic"],
    )
    locked_result = interval(
        [locked_cohort], block_months=block_months, samples=samples, seed=seed + 1
    )
    return {
        "schema_version": 1,
        "analysis": "conditional calendar-month moving-block bootstrap of Spearman rank IC",
        "dataset_id": DATASET_ID,
        "selection_hash": locked["selection_hash"],
        "locked_test_hash": locked["locked_test_hash"],
        "oof_predictions_sha256": selection["oof_predictions_sha256"],
        "locked_scores_sha256": locked["scores_sha256"],
        "block_months": block_months,
        "resamples": samples,
        "seed": seed,
        "confidence_level": 0.95,
        "development": development_result,
        "locked_test": locked_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--block-months", type=int, default=DEFAULT_BLOCK_MONTHS)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    print(
        json.dumps(
            analyze(
                args.root, block_months=args.block_months, samples=args.samples, seed=args.seed
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
