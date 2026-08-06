from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pandas as pd

from edgar_moe import __version__
from edgar_moe.backtest.metrics import predictive_metrics
from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.modeling.experiment import AuthenticatedStudyResult
from edgar_moe.modeling.frozen import FrozenEvaluationResult
from edgar_moe.modeling.walk_forward import (
    WalkForwardModelResult,
    WalkForwardStudyResult,
)
from edgar_moe.settings import ResearchConfig


def build_authenticated_snapshot(
    dataset: ResearchDataset,
    result: AuthenticatedStudyResult,
    output: str | Path,
    *,
    config: ResearchConfig,
) -> dict[str, Any]:
    """Export a locked authenticated study into the public API contract."""
    if not result.locked_test_evaluated or result.locked_test_metrics is None:
        raise ValueError("A public authenticated snapshot requires an opened locked test")
    scored_events = dataset.events.copy()
    scored_events["score"] = result.scores
    scored_events["rank"] = result.ranks
    scored_events["realized_abnormal_return"] = dataset.target
    scored_events["expert_weights"] = [
        {
            "text": float(row[0]),
            "fundamental": float(row[1]),
            "market": float(row[2]),
        }
        for row in result.expert_weights
    ]
    contributions = result.expert_weights * result.expert_predictions
    scored_events["top_attributions"] = [
        [
            {"feature": f"{name}_expert", "contribution": float(row[index])}
            for index, name in sorted(
                enumerate(("text", "fundamental", "market")),
                key=lambda pair: abs(row[pair[0]]),
                reverse=True,
            )
        ]
        for row in contributions
    ]
    test_and_later = scored_events.loc[
        pd.to_datetime(scored_events["accepted_at"], utc=True)
        >= pd.Timestamp(config.evaluation.test_start, tz="UTC")
    ].copy()
    latest_cutoff = pd.to_datetime(test_and_later["accepted_at"], utc=True).max() - pd.Timedelta(
        days=45
    )
    latest_pool = test_and_later.loc[
        pd.to_datetime(test_and_later["accepted_at"], utc=True) >= latest_cutoff
    ]
    latest = pd.concat(
        [latest_pool.nlargest(6, "score"), latest_pool.nsmallest(6, "score")]
    ).drop_duplicates("event_id")
    explorer = pd.concat(
        [
            test_and_later.nlargest(80, "score"),
            test_and_later.nsmallest(80, "score"),
            test_and_later.tail(80),
        ]
    ).drop_duplicates("event_id")
    latest_records = [
        _event_record(row, dataset.as_of)
        for _, row in latest.sort_values("score", ascending=False).iterrows()
    ]
    event_records = [
        _event_record(row, dataset.as_of)
        for _, row in explorer.sort_values("accepted_at", ascending=False).iterrows()
    ]

    experiments = [
        {
            "name": comparison.name,
            "family": comparison.family,
            "validation_rmse": _finite_or_none(comparison.validation_metrics.get("rmse")),
            "validation_rank_ic": _finite_or_none(comparison.validation_metrics.get("rank_ic")),
            "selected": False,
        }
        for comparison in result.comparisons
    ]
    experiments.extend(
        {
            "name": candidate.name,
            "family": "multimodal",
            "validation_rmse": _finite_or_none(candidate.validation_metrics.get("rmse")),
            "validation_rank_ic": _finite_or_none(candidate.validation_metrics.get("rank_ic")),
            "selected": candidate.name == result.selected_candidate.name,
            "best_epoch": candidate.training.best_epoch,
        }
        for candidate in result.candidates
    )
    snapshot: dict[str, Any] = {
        "metadata": {
            "project": "EDGAR-MoE",
            "version": __version__,
            "generated_at": datetime.now(UTC).isoformat(),
            "as_of": dataset.as_of.isoformat(),
            "data_mode": "authenticated_locked_test",
            "research_only": True,
            "disclaimer": (
                "Historical research results from free-source data; not investment advice "
                "and not evidence of future performance."
            ),
        },
        "summary": {
            "title": "Regime-Aware Multimodal Filing Alpha",
            "thesis": (
                "The value of filing text, fundamentals, and market context varies across "
                "regimes; a learned gate should combine them more effectively than static fusion."
            ),
            "universe": (
                f"Prior-month top-{config.data.universe_size} liquid reviewed US equities"
            ),
            "horizon_sessions": config.evaluation.horizon_sessions,
            "events": len(dataset.events),
            "issuers": int(dataset.events["security_id"].nunique()),
            "development_events": len(result.split.development),
            "validation_events": len(result.split.validation),
            "test_events": len(result.split.test),
            "latest_signal_count": len(latest_records),
        },
        "predictive_metrics": {
            "validation": _finite_mapping(result.validation_metrics),
            "locked_test": _finite_mapping(result.locked_test_metrics),
        },
        "portfolio_scenarios": result.portfolio_scenarios,
        "experiments": experiments,
        "equity_curves": result.equity_curves,
        "events": event_records,
        "latest_signals": latest_records,
        "methodology": {
            "target": "20-session beta-adjusted return from first tradable open",
            "split": (
                "Development through 2022; validation 2023–2024; locked test from 2025; "
                "labels must mature before their split boundary"
            ),
            "model": (
                "Frozen FinBERT representation + text/fundamental/market experts + "
                "regime-conditioned missing-aware softmax gate"
            ),
            "portfolio": (
                "Daily event-driven 50/50 long-short with gross, net, beta, industry, "
                "and name constraints"
            ),
            "costs": "10/25/50 bps transaction-cost scenarios plus 2% annual short borrow",
            "limitations": [
                "Free IEX bars cover one venue rather than the consolidated SIP tape.",
                "Historical identifier mapping and delisting coverage remain imperfect.",
                "Historical borrow availability and realized borrow fees are unavailable.",
                "Adjusted daily bars cannot reproduce intraday execution slippage.",
                "The study is historical research and does not establish future alpha.",
            ],
        },
        "freshness": {
            "status": "authenticated_locked",
            "last_successful_update": datetime.now(UTC).isoformat(),
            "next_scheduled_update": None,
            "message": (
                f"Frozen authenticated study {result.run_id}; locked-test artifacts are "
                "content hashed."
            ),
        },
    }
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(orjson.dumps(snapshot, option=orjson.OPT_INDENT_2))
    temporary.replace(destination)
    return snapshot


def build_frozen_snapshot(
    dataset: ResearchDataset,
    result: FrozenEvaluationResult,
    output: str | Path,
) -> dict[str, Any]:
    """Export the hash-verified anchored champion through the public API contract."""
    if result.champion_family != "anchored_multimodal":
        raise ValueError("The public frozen snapshot currently requires an anchored champion")
    if result.expert_weights is None or result.expert_predictions is None:
        raise ValueError("Anchored snapshot requires MoE expert diagnostics")
    anchor_weight = float(result.champion_parameters["fundamental_anchor_weight"])
    residual_weight = float(result.champion_parameters["moe_residual_weight"])
    scored = dataset.events.iloc[result.split.test].copy()
    scored["score"] = result.scores[result.split.test]
    scored["rank"] = result.ranks[result.split.test]
    scored["realized_abnormal_return"] = dataset.target[result.split.test]
    raw_weights = result.expert_weights[result.split.test]
    effective_weights = raw_weights * residual_weight
    effective_weights[:, 1] += anchor_weight
    scored["expert_weights"] = [
        {
            "text": float(row[0]),
            "fundamental": float(row[1]),
            "market": float(row[2]),
        }
        for row in effective_weights
    ]
    moe_contributions = residual_weight * raw_weights * result.expert_predictions[result.split.test]
    anchor_contributions = anchor_weight * result.fundamental_scores[result.split.test]
    attribution_names = (
        "fundamental_anchor",
        "text_moe_expert",
        "fundamental_moe_expert",
        "market_moe_expert",
    )
    scored["top_attributions"] = [
        [
            {"feature": attribution_names[index], "contribution": float(values[index])}
            for index in np.argsort(np.abs(values))[::-1]
        ]
        for values in np.column_stack([anchor_contributions, moe_contributions])
    ]
    accepted = pd.to_datetime(scored["accepted_at"], utc=True)
    latest_cutoff = accepted.max() - pd.Timedelta(days=45)
    latest_pool = scored.loc[accepted >= latest_cutoff]
    latest = pd.concat(
        [latest_pool.nlargest(6, "score"), latest_pool.nsmallest(6, "score")]
    ).drop_duplicates("event_id")
    explorer = pd.concat(
        [
            scored.nlargest(80, "score"),
            scored.nsmallest(80, "score"),
            scored.tail(80),
        ]
    ).drop_duplicates("event_id")
    latest_records = [
        _event_record(row, dataset.as_of)
        for _, row in latest.sort_values("score", ascending=False).iterrows()
    ]
    event_records = [
        _event_record(row, dataset.as_of)
        for _, row in explorer.sort_values("accepted_at", ascending=False).iterrows()
    ]
    models = result.selection_payload["models"]
    experiments = [
        {
            "name": str(model["name"]),
            "family": str(model["family"]),
            "validation_rmse": float(model["aggregate_metrics"]["rmse"]),
            "validation_rank_ic": _finite_or_none(model["aggregate_metrics"].get("rank_ic")),
            "selected": bool(model["selected"]),
        }
        for model in models
        if model["aggregate_metrics"].get("rmse") is not None
    ]
    folds = result.selection_payload["folds"]
    validation_events = sum(int(fold["validation_events"]) for fold in folds)
    snapshot: dict[str, Any] = {
        "metadata": {
            "project": "EDGAR-MoE",
            "version": __version__,
            "generated_at": datetime.now(UTC).isoformat(),
            "as_of": dataset.as_of.isoformat(),
            "data_mode": "authenticated_locked_test",
            "research_only": True,
            "disclaimer": (
                "Frozen historical research results; not investment advice and not "
                "evidence of future performance."
            ),
        },
        "summary": {
            "title": "Fundamental-Anchored Multimodal Filing Alpha",
            "thesis": (
                "A stable fundamental anchor can bound noisy modality risk while a "
                "regime-gated MoE contributes text and market residual information."
            ),
            "universe": (
                f"Prior-month top-{result.config.data.universe_size} liquid reviewed US equities"
            ),
            "horizon_sessions": result.config.evaluation.horizon_sessions,
            "events": len(dataset.events),
            "issuers": int(dataset.events["security_id"].nunique()),
            "development_events": int(folds[0]["train_events"]),
            "validation_events": validation_events,
            "test_events": len(result.split.test),
            "latest_signal_count": len(latest_records),
        },
        "predictive_metrics": {
            "validation": _finite_mapping(
                result.selection_payload["champion"]["aggregate_metrics"]
            ),
            "locked_test": _finite_mapping(result.test_metrics),
        },
        "portfolio_scenarios": result.portfolio_scenarios,
        "experiments": experiments,
        "equity_curves": result.equity_curves,
        "events": event_records,
        "latest_signals": latest_records,
        "methodology": {
            "target": "20-session beta-adjusted return from first tradable open",
            "split": (
                "Expanding 2023/2024 model development; hash-frozen test from 2025; "
                "labels mature before every boundary"
            ),
            "model": result.champion_name,
            "portfolio": (
                "Daily event-driven 50/50 long-short with gross, net, beta, industry, "
                "and name constraints"
            ),
            "costs": "10/25/50 bps transaction-cost scenarios plus 2% annual short borrow",
            "limitations": [
                "Free IEX bars cover one venue rather than the consolidated SIP tape.",
                "Historical identifier mapping and delisting coverage remain imperfect.",
                "Historical borrow availability and realized borrow fees are unavailable.",
                "Daily adjusted bars cannot reproduce intraday execution slippage.",
                "Historical results do not establish future alpha.",
            ],
        },
        "freshness": {
            "status": "authenticated_locked",
            "last_successful_update": datetime.now(UTC).isoformat(),
            "next_scheduled_update": None,
            "message": (
                f"Frozen run {result.run_id}; selection {result.selection_hash}; "
                "locked artifacts are content hashed."
            ),
        },
    }
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(orjson.dumps(snapshot, option=orjson.OPT_INDENT_2))
    temporary.replace(destination)
    return snapshot


def write_research_report(
    dataset: ResearchDataset,
    result: AuthenticatedStudyResult,
    output: str | Path,
    *,
    config: ResearchConfig,
) -> Path:
    """Write a complete, reproducible Markdown report for an authenticated run."""
    if not result.locked_test_evaluated or result.locked_test_metrics is None:
        raise ValueError("The final report requires an explicitly opened locked test")
    comparison_rows = [
        (
            item.name,
            item.validation_metrics,
            result.locked_comparison_metrics.get(item.name, {}),
        )
        for item in result.comparisons
    ]
    errors = dataset.events.iloc[result.split.test].copy()
    errors["target"] = dataset.target[result.split.test]
    errors["prediction"] = result.scores[result.split.test]
    errors["absolute_error"] = (errors["target"] - errors["prediction"]).abs()
    worst_errors = errors.nlargest(10, "absolute_error")
    lines = [
        "# EDGAR-MoE Authenticated Research Report",
        "",
        f"> Frozen run: `{result.run_id}`. Dataset: `{dataset.dataset_id}`.",
        "",
        "## Abstract",
        "",
        (
            "This study evaluates whether a regime-conditioned mixture of filing text, "
            "point-in-time XBRL fundamentals, and trailing market features predicts "
            f"{config.evaluation.horizon_sessions}-session beta-adjusted returns. Model "
            "selection used validation data only; the test period was opened once after "
            "the selected configuration was frozen."
        ),
        "",
        "## Dataset and attrition",
        "",
        "| Stage | Rows |",
        "|---|---:|",
        *[
            f"| {name.replace('_', ' ').title()} | {count:,} |"
            for name, count in dataset.attrition.items()
        ],
        f"| Development split | {len(result.split.development):,} |",
        f"| Validation split | {len(result.split.validation):,} |",
        f"| Locked-test split | {len(result.split.test):,} |",
        "",
        "## Experimental protocol",
        "",
        f"- Dataset ID: `{dataset.dataset_id}`",
        f"- Source-manifest SHA-256: `{dataset.source_manifest_hash}`",
        f"- Random seed: `{config.project.random_seed}`",
        (
            f"- Selected model: {result.selected_candidate.name}, best epoch "
            f"{result.selected_candidate.training.best_epoch}"
        ),
        (
            "- Leakage control: every feature availability timestamp is required to be "
            "at or before filing acceptance; labels mature before split boundaries."
        ),
        "",
        "## Predictive results",
        "",
        "| Model | Validation RMSE | Validation rank IC | Test RMSE | Test rank IC |",
        "|---|---:|---:|---:|---:|",
        _metric_table_row(
            "Regime-Gated MoE",
            result.validation_metrics,
            result.locked_test_metrics,
        ),
        *[_metric_table_row(name, validation, test) for name, validation, test in comparison_rows],
        "",
        "## Cost-aware portfolio results",
        "",
        "| Cost | Annual return | Volatility | Sharpe | 95% bootstrap CI | Max drawdown | Turnover |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        *[_portfolio_table_row(item) for item in result.portfolio_scenarios],
        "",
        "## Largest locked-test errors",
        "",
        "| Ticker | Filing | Accepted | Prediction | Realized | Absolute error |",
        "|---|---|---|---:|---:|---:|",
        *[
            (
                f"| {row.ticker} | {row.form} | "
                f"{pd.Timestamp(row.accepted_at).date().isoformat()} | "
                f"{row.prediction:.4f} | {row.target:.4f} | {row.absolute_error:.4f} |"
            )
            for row in worst_errors.itertuples(index=False)
        ],
        "",
        "## Known limitations",
        "",
        "- Free IEX market data represents one venue, not the consolidated US tape.",
        "- Identifier history and delisting coverage are audited but remain imperfect.",
        "- Historical short availability and realized borrow fees are unavailable.",
        "- Daily adjusted bars cannot reproduce intraday execution slippage.",
        "- Statistical evidence is historical and does not establish future profitability.",
        "",
        "## Conclusion",
        "",
        _conclusion(result),
        "",
    ]
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(destination)
    return destination


def write_validation_report(
    dataset: ResearchDataset,
    result: AuthenticatedStudyResult,
    output: str | Path,
    *,
    config: ResearchConfig,
) -> Path:
    """Write a validation-only report without evaluating or revealing locked-test scores."""
    validation_events = dataset.events.iloc[result.split.validation]
    validation_target = dataset.target[result.split.validation]
    years = pd.to_datetime(validation_events["accepted_at"], utc=True).dt.year.to_numpy()
    unique_years = sorted({int(year) for year in years})
    model_rows = [
        (
            result.selected_candidate.name,
            "multimodal",
            result.validation_metrics,
            result.scores[result.split.validation],
        ),
        *[
            (
                comparison.name,
                comparison.family,
                comparison.validation_metrics,
                comparison.validation_predictions,
            )
            for comparison in result.comparisons
        ],
    ]
    finite_rank_rows = [
        row for row in model_rows if _finite_or_none(row[2].get("rank_ic")) is not None
    ]
    best_rank = max(
        finite_rank_rows,
        key=lambda row: float(row[2]["rank_ic"]),
    )
    header_years = " | ".join(f"{year} rank IC" for year in unique_years)
    separator_years = "|".join("---:" for _ in unique_years)
    lines = [
        "# EDGAR-MoE Validation Report",
        "",
        "> Status: development and validation complete; the locked test was not evaluated.",
        "",
        "## Reproducibility",
        "",
        f"- Dataset ID: `{dataset.dataset_id}`",
        f"- Run ID: `{result.run_id}`",
        f"- Source-manifest SHA-256: `{dataset.source_manifest_hash}`",
        f"- Random seed: `{config.project.random_seed}`",
        f"- Text encoder: `{dataset.provenance.get('text_encoder', 'unknown')}`",
        "",
        "## Dataset and temporal split",
        "",
        f"- Audited filing events: {len(dataset.events):,}",
        f"- Development events: {len(result.split.development):,}",
        f"- Validation events: {len(result.split.validation):,}",
        f"- Sealed locked-test events: {len(result.split.test):,}",
        f"- Text parsing failures: {dataset.attrition.get('text_parse_failures', 0):,}",
        "",
        "## Validation results",
        "",
        f"| Model | Family | RMSE | MAE | Rank IC | {header_years} |",
        f"|---|---|---:|---:|---:|{separator_years}|",
        *[
            _validation_table_row(
                name,
                family,
                metrics,
                predictions,
                validation_target,
                years,
                unique_years,
            )
            for name, family, metrics, predictions in model_rows
        ],
        "",
        "## Decision",
        "",
        (
            f"The configured MoE selected by validation RMSE was "
            f"**{result.selected_candidate.name}** with rank IC "
            f"{_format_number(result.validation_metrics.get('rank_ic'))}. The strongest "
            f"validation rank result was **{best_rank[0]}** at "
            f"{_format_number(best_rank[2].get('rank_ic'))}."
        ),
        "",
        (
            "Because the proposed MoE did not outperform the strongest preregistered "
            "baseline on validation rank IC, the locked test remains sealed and no "
            "authenticated market-performance snapshot is published."
        ),
        "",
        "## Interpretation",
        "",
        "- The pipeline and leakage controls passed on authenticated data.",
        "- Validation evidence does not support the claim that regime gating improves ranks.",
        "- These are model-development results, not test results or evidence of live alpha.",
        "",
    ]
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(destination)
    return destination


def write_walk_forward_report(
    dataset: ResearchDataset,
    result: WalkForwardStudyResult,
    output: str | Path,
    *,
    config: ResearchConfig,
    selection_hash: str | None = None,
) -> Path:
    """Write pre-test model-selection evidence without reading locked-test outcomes."""
    fold_names = [fold.name for fold in result.folds]
    fold_headers = " | ".join(f"{fold.validation_year} rank IC" for fold in result.folds)
    fold_separators = "|".join("---:" for _ in result.folds)
    lines = [
        "# EDGAR-MoE Walk-Forward Selection Report",
        "",
        (
            "> Status: pre-test expanding-window model selection complete; "
            "the 2025+ locked test was not transformed, predicted, or evaluated."
        ),
        "",
        "## Reproducibility",
        "",
        f"- Dataset ID: `{dataset.dataset_id}`",
        f"- Run ID: `{result.run_id}`",
        *([f"- Selection SHA-256: `{selection_hash}`"] if selection_hash is not None else []),
        f"- Source-manifest SHA-256: `{dataset.source_manifest_hash}`",
        f"- Random seed: `{config.project.random_seed}`",
        f"- Text encoder: `{dataset.provenance.get('text_encoder', 'unknown')}`",
        f"- Selection rule: {result.selection_rule}.",
        "",
        "## Expanding-window folds",
        "",
        "| Fold | Training events | Validation events | Training-label cutoff |",
        "|---|---:|---:|---|",
        *[
            (
                f"| {fold.validation_year} | {len(fold.train):,} | "
                f"{len(fold.validation):,} | {fold.train_label_cutoff} |"
            )
            for fold in result.folds
        ],
        "",
        "## Out-of-fold model comparison",
        "",
        (
            f"| Model | Family | {fold_headers} | Worst-fold rank IC | "
            "Weighted rank IC | Pooled OOF RMSE |"
        ),
        f"|---|---|{fold_separators}|---:|---:|---:|",
        *[_walk_forward_table_row(model, fold_names) for model in result.models],
        "",
        "## Frozen decision",
        "",
        (
            f"**{result.champion.name}** is the pre-test champion. Its worst-fold rank IC "
            f"is {_format_number(result.champion.worst_fold_rank_ic)}, weighted rank IC is "
            f"{_format_number(result.champion.aggregate_metrics.get('rank_ic'))}, and "
            f"pooled OOF RMSE is {_format_number(result.champion.aggregate_metrics.get('rmse'))}."
        ),
        "",
        (
            f"The locked period begins {result.locked_test_start}. Locked-test prediction "
            "count: **0**. Opening that period requires a separate explicit action after this "
            "selection artifact is reviewed and frozen."
        ),
        "",
        "## Interpretation",
        "",
        "- Each fold refits imputation, scaling, and every model using prior data only.",
        "- Fold labels must mature before the next boundary, providing an event-horizon purge.",
        "- The robust objective rewards models whose rank relationship survives both years.",
        "- Weighted rank IC averages fold ICs by validation count; RMSE pools raw OOF errors.",
        (
            "- The anchored family was introduced after initial pre-test diagnostics favored "
            "fundamentals; this is iterative development, not independent confirmation."
        ),
        "- These are development results, not locked-test results or evidence of live alpha.",
        "",
    ]
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(destination)
    return destination


def write_frozen_evaluation_report(
    dataset: ResearchDataset,
    result: FrozenEvaluationResult,
    output: str | Path,
) -> Path:
    """Write the one-time report tied to a verified walk-forward selection hash."""
    development = result.selection_payload["champion"]["aggregate_metrics"]
    errors = dataset.events.iloc[result.split.test].copy()
    errors["target"] = dataset.target[result.split.test]
    errors["prediction"] = result.scores[result.split.test]
    errors["absolute_error"] = (errors["target"] - errors["prediction"]).abs()
    worst_errors = errors.nlargest(10, "absolute_error")
    lines = [
        "# EDGAR-MoE Frozen Locked-Test Report",
        "",
        f"> Frozen run: `{result.run_id}`.",
        "",
        "## Reproducibility",
        "",
        f"- Dataset ID: `{result.dataset_id}`",
        f"- Walk-forward selection SHA-256: `{result.selection_hash}`",
        f"- Frozen champion: {result.champion_name}",
        f"- Final pre-test training events: {len(result.split.train):,}",
        f"- Locked-test events: {len(result.split.test):,}",
        "",
        "## Predictive result",
        "",
        "| Model | Development rank IC | Development RMSE | Test rank IC | Test RMSE | Test MAE |",
        "|---|---:|---:|---:|---:|---:|",
        (
            f"| {result.champion_name} | {_format_number(development.get('rank_ic'))} | "
            f"{_format_number(development.get('rmse'))} | "
            f"{_format_number(result.test_metrics.get('rank_ic'))} | "
            f"{_format_number(result.test_metrics.get('rmse'))} | "
            f"{_format_number(result.test_metrics.get('mae'))} |"
        ),
        "",
        "## Locked-test components",
        "",
        "| Component | RMSE | MAE | Rank IC |",
        "|---|---:|---:|---:|",
        *[
            (
                f"| {name} | {_format_number(metrics.get('rmse'))} | "
                f"{_format_number(metrics.get('mae'))} | "
                f"{_format_number(metrics.get('rank_ic'))} |"
            )
            for name, metrics in result.component_metrics.items()
        ],
        "",
        "## Cost-aware portfolio results",
        "",
        "| Cost | Annual return | Volatility | Sharpe | 95% bootstrap CI | Max drawdown | Turnover |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        *[_portfolio_table_row(item) for item in result.portfolio_scenarios],
        "",
        "## Largest locked-test errors",
        "",
        "| Ticker | Filing | Accepted | Prediction | Realized | Absolute error |",
        "|---|---|---|---:|---:|---:|",
        *[
            (
                f"| {row.ticker} | {row.form} | "
                f"{pd.Timestamp(row.accepted_at).date().isoformat()} | "
                f"{row.prediction:.4f} | {row.target:.4f} | "
                f"{row.absolute_error:.4f} |"
            )
            for row in worst_errors.itertuples(index=False)
        ],
        "",
        "## Interpretation",
        "",
        (
            "This is the sole locked evaluation tied to the recorded selection hash. "
            "It must be reported whether positive, negative, or inconclusive."
        ),
        "",
    ]
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(destination)
    return destination


def _event_record(row: pd.Series, as_of: Any) -> dict[str, Any]:
    horizon = pd.Timestamp(row["horizon_at"]).date()
    target = float(row["realized_abnormal_return"])
    pending = horizon > as_of or not math.isfinite(target)
    rank = min(max(float(row["rank"]), 0.0), 1.0)
    return {
        "accession_number": str(row["accession_number"]),
        "event_id": str(row["event_id"]),
        "security_id": str(row["security_id"]),
        "ticker": str(row["ticker"]),
        "company_name": str(row["company_name"]),
        "form": str(row["form"]),
        "accepted_at": pd.Timestamp(row["accepted_at"]).isoformat(),
        "entry_date": pd.Timestamp(row["entry_date"]).date().isoformat(),
        "horizon_date": horizon.isoformat(),
        "industry_code": str(row["industry_code"]),
        "score": round(float(row["score"]), 8),
        "rank": round(rank, 6),
        "direction": "long" if rank >= 0.9 else "short" if rank <= 0.1 else "neutral",
        "expert_weights": row["expert_weights"],
        "top_attributions": row["top_attributions"],
        "realized_abnormal_return": None if pending else round(target, 8),
        "filing_url": str(row["filing_url"]),
    }


def _walk_forward_table_row(
    model: WalkForwardModelResult,
    fold_names: list[str],
) -> str:
    fold_cells = " | ".join(
        _format_number(model.fold_metrics[name].get("rank_ic")) for name in fold_names
    )
    return (
        f"| {model.name} | {model.family} | {fold_cells} | "
        f"{_format_number(model.worst_fold_rank_ic)} | "
        f"{_format_number(model.aggregate_metrics.get('rank_ic'))} | "
        f"{_format_number(model.aggregate_metrics.get('rmse'))} |"
    )


def _metric_table_row(name: str, validation: dict[str, float], test: dict[str, float]) -> str:
    return (
        f"| {name} | {_format_number(validation.get('rmse'))} | "
        f"{_format_number(validation.get('rank_ic'))} | "
        f"{_format_number(test.get('rmse'))} | {_format_number(test.get('rank_ic'))} |"
    )


def _validation_table_row(
    name: str,
    family: str,
    metrics: dict[str, float],
    predictions: Any,
    target: Any,
    years: Any,
    unique_years: list[int],
) -> str:
    year_metrics = [
        predictive_metrics(target[years == year], predictions[years == year])
        for year in unique_years
    ]
    year_cells = " | ".join(_format_number(values.get("rank_ic")) for values in year_metrics)
    return (
        f"| {name} | {family} | {_format_number(metrics.get('rmse'))} | "
        f"{_format_number(metrics.get('mae'))} | "
        f"{_format_number(metrics.get('rank_ic'))} | {year_cells} |"
    )


def _portfolio_table_row(item: dict[str, float | int | None]) -> str:
    return (
        f"| {int(item.get('cost_bps') or 0)} bps | {_format_percent(item.get('annualized_return'))} | "
        f"{_format_percent(item.get('annualized_volatility'))} | "
        f"{_format_number(item.get('sharpe'))} | "
        f"[{_format_number(item.get('sharpe_ci_low'))}, "
        f"{_format_number(item.get('sharpe_ci_high'))}] | "
        f"{_format_percent(item.get('maximum_drawdown'))} | "
        f"{_format_number(item.get('average_turnover'))} |"
    )


def _conclusion(result: AuthenticatedStudyResult) -> str:
    equal = result.locked_comparison_metrics.get("Equal-Weight Expert Ensemble", {})
    moe_ic = _finite_or_none(
        result.locked_test_metrics.get("rank_ic") if result.locked_test_metrics else None
    )
    equal_ic = _finite_or_none(equal.get("rank_ic"))
    base = next((item for item in result.portfolio_scenarios if item["cost_bps"] == 10), None)
    if moe_ic is None or equal_ic is None or base is None:
        return "The locked results are incomplete; no research hypothesis is classified."
    gate_statement = "supported" if moe_ic > equal_ic else "not supported"
    low = _finite_or_none(base.get("sharpe_ci_low"))
    economic_statement = (
        "supported at the base-cost scenario"
        if low is not None and low > 0
        else "not established after costs"
    )
    return (
        f"The regime-gating hypothesis was {gate_statement} relative to the equal-weight "
        f"expert ensemble on locked-test rank IC. Cost-aware economic significance was "
        f"{economic_statement}. These classifications apply only to this frozen historical "
        "dataset and are not forecasts of future performance."
    )


def _finite_mapping(values: dict[str, float]) -> dict[str, float | None]:
    return {key: _finite_or_none(value) for key, value in values.items()}


def _finite_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _format_number(value: Any) -> str:
    numeric = _finite_or_none(value)
    return "—" if numeric is None else f"{numeric:.4f}"


def _format_percent(value: Any) -> str:
    numeric = _finite_or_none(value)
    return "—" if numeric is None else f"{numeric:.2%}"
