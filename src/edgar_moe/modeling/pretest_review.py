"""Identity-checked, pre-test-only review of a separate duration-aware study.

This reads a processed v2 dataset and its saved walk-forward score archive. The
dataset loader reads the complete target array, but this review never indexes,
scores, or evaluates locked-period labels. It does not retrain, publish a
snapshot, or write a forecast.
All reported model differences are conditional on the same development-era
selection and therefore are not independent evidence of an alpha.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pandas as pd

from edgar_moe.backtest.engine import run_event_backtest
from edgar_moe.backtest.metrics import predictive_metrics
from edgar_moe.data.storage import sha256_file
from edgar_moe.features.dataset import ResearchDataset, dataset_xbrl_fact_policy
from edgar_moe.modeling.experiment import portfolio_outputs
from edgar_moe.modeling.walk_forward import (
    PROTOCOL_REVISIONS,
    WalkForwardFold,
    make_walk_forward_folds,
)
from edgar_moe.settings import ResearchConfig

SIMPLE_COMPARATORS = (
    "Elastic Net",
    "Gradient-Boosted Tabular",
    "Fundamental-Only Expert",
    "Static Early-Fusion MLP",
    "Equal-Weight Expert Ensemble",
)


def review_v2_pretest(
    dataset: ResearchDataset,
    selection_dir: Path,
    *,
    bootstrap_samples: int = 1000,
    block_months: int = 2,
    seed: int = 42,
) -> dict[str, Any]:
    """Verify the saved selection and compare paired OOF rank IC by time block."""
    if not 100 <= bootstrap_samples <= 5000 or not 1 <= block_months <= 6:
        raise ValueError("bootstrap budget or calendar block length is outside reviewed bounds")
    if dataset_xbrl_fact_policy(dataset) != "duration_aware_v2":
        raise ValueError("v2 review requires a duration_aware_v2 dataset")

    selection_path = selection_dir / "walk-forward-selection.json"
    payload = orjson.loads(selection_path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError("selection must be a JSON object")
    recorded_hash = payload.get("selection_hash")
    unsigned = {key: value for key, value in payload.items() if key != "selection_hash"}
    observed_hash = hashlib.sha256(orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)).hexdigest()
    if recorded_hash != observed_hash:
        raise ValueError("walk-forward selection hash mismatch")
    if payload.get("dataset_id") != dataset.dataset_id:
        raise ValueError("selection and dataset identities differ")
    if payload.get("protocol_revisions") != PROTOCOL_REVISIONS:
        raise ValueError("selection lacks the reviewed post-v1 protocol")
    if (
        payload.get("locked_test_evaluated") is not False
        or payload.get("locked_test_prediction_count") != 0
    ):
        raise ValueError("selection is not pre-test-only")
    config = ResearchConfig.model_validate(payload["configuration"])
    if config.features.xbrl_fact_policy != "duration_aware_v2":
        raise ValueError("selection configuration is not duration_aware_v2")
    if payload.get("locked_test_start") != config.evaluation.test_start:
        raise ValueError("locked-test boundary differs from the selection configuration")

    folds = make_walk_forward_folds(dataset, config)
    if payload.get("folds") != [_fold_record(fold) for fold in folds]:
        raise ValueError("saved fold boundaries differ from the dataset")
    expected_indices = np.concatenate([fold.validation for fold in folds])
    score_path = selection_dir / "oof-predictions.npz"
    score_hash = sha256_file(score_path)
    if payload.get("oof_predictions_sha256") != score_hash:
        raise ValueError("out-of-fold score archive hash mismatch")
    with np.load(score_path, allow_pickle=False) as archive:
        names = archive["model_names"].tolist()
        scores = np.asarray(archive["predictions"], dtype=np.float64)
        indices = np.asarray(archive["oof_indices"], dtype=np.int64)
    models = payload.get("models")
    if not isinstance(models, list) or not all(isinstance(item, dict) for item in models):
        raise ValueError("selection model roster is invalid")
    if (
        not isinstance(names, list)
        or len(names) != len(set(names))
        or names != [item.get("name") for item in models]
        or scores.shape != (len(dataset.events), len(names))
        or not np.array_equal(indices, expected_indices)
    ):
        raise ValueError("score archive and selection roster do not align")
    outside = np.ones(len(dataset.events), dtype=bool)
    outside[indices] = False
    if not np.isnan(scores[outside]).all() or not np.isfinite(scores[indices]).all():
        raise ValueError("score archive contains missing OOF or non-OOF predictions")
    if not np.isfinite(dataset.target[indices]).all():
        raise ValueError("out-of-fold labels are incomplete")

    champion = payload.get("champion")
    selected = [item for item in models if item.get("selected") is True]
    if not isinstance(champion, dict) or len(selected) != 1 or selected[0] != champion:
        raise ValueError("selection has no unique champion")
    if not set(SIMPLE_COMPARATORS).issubset(names):
        raise ValueError("selection lacks required simple comparators")

    metrics: dict[str, dict[str, float]] = {}
    for position, model in enumerate(models):
        fold_metrics = {
            fold.name: predictive_metrics(
                dataset.target[fold.validation], scores[fold.validation, position]
            )
            for fold in folds
        }
        pooled = predictive_metrics(dataset.target[indices], scores[indices, position])
        weighted_rank_ic = float(
            np.average(
                [fold_metrics[fold.name]["rank_ic"] for fold in folds],
                weights=[len(fold.validation) for fold in folds],
            )
        )
        _assert_metrics(model, fold_metrics, pooled, weighted_rank_ic)
        metrics[str(model["name"])] = {**pooled, "weighted_rank_ic": weighted_rank_ic}

    champion_name = str(champion["name"])
    champion_score = scores[:, names.index(champion_name)]
    comparisons = []
    for baseline_name in SIMPLE_COMPARATORS:
        baseline_score = scores[:, names.index(baseline_name)]
        delta, interval = _paired_delta_interval(
            dataset,
            folds,
            champion_score,
            baseline_score,
            samples=bootstrap_samples,
            block_months=block_months,
            seed=seed,
        )
        comparisons.append(
            {
                "baseline": baseline_name,
                "baseline_weighted_rank_ic": metrics[baseline_name]["weighted_rank_ic"],
                "champion_minus_baseline_rank_ic": delta,
                "delta_interval_95": interval,
            }
        )
    portfolio = _portfolio_cost_review(dataset, indices, names, scores, champion_name, config)
    return {
        "schema_version": 1,
        "status": "pretest_development_only",
        "dataset_id": dataset.dataset_id,
        "xbrl_fact_policy": "duration_aware_v2",
        "source_manifest_sha256": dataset.source_manifest_hash,
        "selection_sha256": recorded_hash,
        "oof_predictions_sha256": score_hash,
        "locked_test_start": config.evaluation.test_start,
        "locked_test_predictions": 0,
        "oof_events": len(indices),
        "folds": [{"name": fold.name, "validation_events": len(fold.validation)} for fold in folds],
        "champion": {
            "name": champion_name,
            "family": champion["family"],
            **metrics[champion_name],
        },
        "comparisons": comparisons,
        "portfolio": portfolio,
        "uncertainty": {
            "method": "paired_calendar_month_moving_block_within_fold",
            "block_months": block_months,
            "resamples": bootstrap_samples,
            "seed": seed,
            "conditional_on_selection": True,
        },
        "interpretation": (
            "Development-era paired comparisons only; candidate selection used these same "
            "folds. No independent rank-skill, tradable-alpha, or LLM-value claim is supported."
        ),
    }


def _portfolio_cost_review(
    dataset: ResearchDataset,
    indices: np.ndarray,
    names: list[str],
    scores: np.ndarray,
    champion_name: str,
    config: ResearchConfig,
) -> dict[str, Any]:
    """Evaluate pre-locked OOF signals only when every active return is present."""
    locked_start = pd.Timestamp(config.evaluation.test_start)
    selected = dataset.events.iloc[indices].copy()
    entries = pd.to_datetime(selected["entry_date"], utc=True).dt.tz_localize(None).dt.normalize()
    exits = pd.to_datetime(selected["exit_date"], utc=True).dt.tz_localize(None).dt.normalize()
    eligible = (entries < locked_start) & (exits < locked_start) & (exits >= entries)
    excluded = int((~eligible).sum())
    selected = selected.loc[eligible]
    if selected.empty:
        return {"status": "unavailable_no_prelocked_signals", "excluded_events": excluded}

    returns = dataset.daily_returns.copy()
    returns["date"] = pd.to_datetime(returns["date"], utc=True).dt.tz_localize(None).dt.normalize()
    window_start = entries.loc[eligible].min()
    window_end = exits.loc[eligible].max()
    returns = returns.loc[(returns["date"] >= window_start) & (returns["date"] <= window_end)]
    dates = pd.DatetimeIndex(sorted(returns["date"].unique()))
    maximum_gap = max((dates[1:] - dates[:-1]), default=pd.Timedelta(0))
    if (
        len(dates) < 30
        or dates.min() > window_start + pd.Timedelta(days=7)
        or dates.max() < window_end - pd.Timedelta(days=7)
        or maximum_gap > pd.Timedelta(days=10)
        or (returns["date"] >= locked_start).any()
    ):
        return {
            "status": "unavailable_return_calendar",
            "eligible_events": len(selected),
            "excluded_events": excluded,
            "observed_trading_days": len(dates),
        }
    if returns.duplicated(["date", "security_id"]).any():
        raise ValueError("duplicate security-day returns prevent a portfolio review")
    required_return_columns = ["return"]
    if {"overnight_return", "intraday_return"}.issubset(returns.columns):
        required_return_columns.extend(["overnight_return", "intraday_return"])
    for column in required_return_columns:
        if not np.isfinite(pd.to_numeric(returns[column], errors="coerce")).all():
            raise ValueError(f"nonfinite {column} prevents a portfolio review")
    available = set(zip(returns["date"], returns["security_id"], strict=True))
    missing = 0
    for row in selected.itertuples():
        start = pd.Timestamp(row.entry_date)
        end = pd.Timestamp(row.exit_date)
        for day in dates[(dates >= start) & (dates <= end)]:
            if (day, row.security_id) not in available:
                missing += 1
    if missing:
        return {
            "status": "unavailable_incomplete_return_coverage",
            "eligible_events": len(selected),
            "excluded_events": excluded,
            "missing_active_security_days": missing,
        }

    results = []
    for name in dict.fromkeys((champion_name, "Fundamental-Only Expert")):
        signals = selected[
            ["entry_date", "exit_date", "security_id", "ticker", "beta", "industry_code"]
        ].copy()
        signals["score"] = scores[indices[eligible.to_numpy()], names.index(name)]
        backtest = run_event_backtest(signals, returns, config.portfolio)
        scenarios, _curves = portfolio_outputs(backtest, config, seed=config.project.random_seed)
        results.append({"model": name, "scenarios": scenarios})
    return {
        "status": "development_only",
        "eligible_events": len(selected),
        "excluded_events": excluded,
        "observed_trading_days": len(dates),
        "cost_definition": "10/25/50 bps per unit of one-sided turnover plus configured short borrow",
        "models": results,
        "fundamental_is_champion": champion_name == "Fundamental-Only Expert",
        "caveat": "Same selected development folds; not an independent or live trading estimate.",
    }


def _fold_record(fold: WalkForwardFold) -> dict[str, Any]:
    return {
        "name": fold.name,
        "validation_year": fold.validation_year,
        "train_events": len(fold.train),
        "validation_events": len(fold.validation),
        "train_label_cutoff": fold.train_label_cutoff,
        "validation_start": fold.validation_start,
        "validation_end": fold.validation_end,
    }


def _assert_metrics(
    model: dict[str, Any],
    folds: dict[str, dict[str, float]],
    pooled: dict[str, float],
    weighted_rank_ic: float,
) -> None:
    saved_folds = model.get("fold_metrics")
    saved_aggregate = model.get("aggregate_metrics")
    if not isinstance(saved_folds, dict) or not isinstance(saved_aggregate, dict):
        raise ValueError("saved model metrics are missing")
    for name, values in folds.items():
        for metric, observed in values.items():
            _assert_close(observed, saved_folds[name][metric])
    for metric in ("rmse", "mae"):
        _assert_close(pooled[metric], saved_aggregate[metric])
    _assert_close(weighted_rank_ic, saved_aggregate["rank_ic"])


def _assert_close(observed: float, saved: object) -> None:
    if (
        not isinstance(saved, (int, float))
        or not math.isfinite(observed)
        or not math.isclose(observed, float(saved), rel_tol=0, abs_tol=1e-9)
    ):
        raise ValueError("saved OOF metrics do not reproduce")


def _paired_delta_interval(
    dataset: ResearchDataset,
    folds: list[WalkForwardFold],
    champion: np.ndarray,
    baseline: np.ndarray,
    *,
    samples: int,
    block_months: int,
    seed: int,
) -> tuple[float, dict[str, Any]]:
    prepared: list[tuple[np.ndarray, np.ndarray, np.ndarray, list[np.ndarray]]] = []
    point_deltas: list[float] = []
    counts: list[int] = []
    months_per_fold: list[int] = []
    for fold in folds:
        indices = fold.validation
        months = (
            pd.to_datetime(dataset.events.iloc[indices]["accepted_at"], utc=True)
            .dt.tz_localize(None)
            .to_numpy(dtype="datetime64[M]")
        )
        calendar = np.arange(
            months.min(), months.max() + np.timedelta64(1, "M"), dtype="datetime64[M]"
        )
        blocks = [np.flatnonzero(months == month) for month in calendar]
        target = dataset.target[indices]
        complex_score = champion[indices]
        simple_score = baseline[indices]
        prepared.append((target, complex_score, simple_score, blocks))
        point_deltas.append(_rank_ic(target, complex_score) - _rank_ic(target, simple_score))
        counts.append(len(indices))
        months_per_fold.append(len(blocks))
    point = float(np.average(point_deltas, weights=counts))
    unavailable = {"low": None, "high": None, "calendar_months_per_fold": months_per_fold}
    if any(length < 2 * block_months for length in months_per_fold):
        return point, {"status": "insufficient_calendar_months", **unavailable}
    rng = np.random.default_rng(seed)
    draws: list[float] = []
    for _ in range(samples):
        deltas = []
        for target, complex_score, simple_score, blocks in prepared:
            selected: list[np.ndarray] = []
            remaining = len(blocks)
            while remaining:
                start = int(rng.integers(0, len(blocks) - block_months + 1))
                take = min(block_months, remaining)
                selected.extend(blocks[start : start + take])
                remaining -= take
            draw = np.concatenate(selected)
            if len(draw) < 3:
                break
            complex_ic = _rank_ic(target[draw], complex_score[draw], strict=False)
            simple_ic = _rank_ic(target[draw], simple_score[draw], strict=False)
            if not math.isfinite(complex_ic) or not math.isfinite(simple_ic):
                break
            deltas.append(complex_ic - simple_ic)
        if len(deltas) == len(folds):
            draws.append(float(np.average(deltas, weights=counts)))
    if len(draws) < math.ceil(samples * 0.95):
        return point, {
            "status": "degenerate_resamples",
            "valid_resamples": len(draws),
            **unavailable,
        }
    low, high = np.quantile(draws, [0.025, 0.975])
    return point, {
        "status": "ready",
        "low": float(low),
        "high": float(high),
        "valid_resamples": len(draws),
        "calendar_months_per_fold": months_per_fold,
    }


def _rank_ic(target: np.ndarray, score: np.ndarray, *, strict: bool = True) -> float:
    value = predictive_metrics(target, score)["rank_ic"]
    if strict and not math.isfinite(value):
        raise ValueError("rank IC is undefined for a scored fold")
    return value
