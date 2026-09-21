from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pandas as pd
from sklearn.preprocessing import StandardScaler

from edgar_moe.backtest.engine import run_event_backtest
from edgar_moe.backtest.metrics import (
    block_bootstrap_sharpe_interval,
    performance_metrics,
    predictive_metrics,
)
from edgar_moe.modeling.baselines import train_baselines
from edgar_moe.modeling.moe import RegimeGatedMoE, torch
from edgar_moe.modeling.preprocess import MultimodalPreprocessor
from edgar_moe.modeling.train import predict_moe, train_moe
from edgar_moe.settings import ResearchConfig

DEMO_AS_OF = pd.Timestamp("2026-07-31")


@dataclass(frozen=True)
class SyntheticDataset:
    events: pd.DataFrame
    modalities: dict[str, np.ndarray]
    regime: np.ndarray
    target: np.ndarray
    daily_returns: pd.DataFrame


def generate_synthetic_dataset(seed: int = 42, securities: int = 300) -> SyntheticDataset:
    """Generate a structured fixture with genuine regime-dependent modality effects."""
    generator = np.random.default_rng(seed)
    security_ids = np.array([f"SEC-{index:04d}" for index in range(securities)])
    tickers = np.array([f"Q{index:03d}" for index in range(securities)])
    industries = np.array([f"SIC-{index % 10:02d}" for index in range(securities)])
    betas = generator.normal(1.0, 0.18, size=securities).clip(0.35, 1.7)

    event_rows: list[dict[str, Any]] = []
    quarter_ends = pd.date_range("2018-03-31", "2026-06-30", freq="QE")
    for quarter_number, quarter_end in enumerate(quarter_ends):
        base_date = quarter_end + pd.Timedelta(days=18)
        offsets = generator.integers(0, 42, size=securities)
        for security_index, offset in enumerate(offsets):
            accepted = base_date + pd.offsets.BDay(int(offset))
            accepted_at = pd.Timestamp(accepted).tz_localize("America/New_York") + pd.Timedelta(
                hours=17, minutes=int(generator.integers(0, 45))
            )
            entry_date = (accepted + pd.offsets.BDay(1)).normalize()
            exit_date = (entry_date + pd.offsets.BDay(19)).normalize()
            event_rows.append(
                {
                    "event_id": f"DEMO-{quarter_number:02d}-{security_index:04d}",
                    "accession_number": f"0000000000-{18 + quarter_number // 4:02d}-{quarter_number * securities + security_index:06d}",
                    "security_id": security_ids[security_index],
                    "ticker": tickers[security_index],
                    "company_name": f"Synthetic Research Company {security_index:03d}",
                    "industry_code": industries[security_index],
                    "beta": float(betas[security_index]),
                    "form": "10-K" if quarter_number % 4 == 3 else "10-Q",
                    "accepted_at": accepted_at.tz_convert("UTC"),
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "filing_url": "https://www.sec.gov/edgar/search/",
                }
            )
    events = pd.DataFrame(event_rows).sort_values("accepted_at").reset_index(drop=True)
    accepted_utc = pd.to_datetime(events["accepted_at"], utc=True)
    events = events.loc[
        accepted_utc
        <= DEMO_AS_OF.tz_localize("UTC") + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    ].reset_index(drop=True)
    rows = len(events)

    text = generator.normal(size=(rows, 16))
    fundamental = generator.normal(size=(rows, 10))
    market = generator.normal(size=(rows, 8))
    date_index = pd.to_datetime(events["accepted_at"], utc=True)
    year_phase = (date_index.dt.dayofyear.to_numpy() / 365.25) * 2 * np.pi
    crisis = (
        (date_index.dt.year.to_numpy() == 2020) | (date_index.dt.year.to_numpy() == 2022)
    ).astype(float)
    regime = np.column_stack(
        [
            np.sin(year_phase),
            np.cos(year_phase),
            crisis + generator.normal(0, 0.1, rows),
            generator.normal(0, 1, rows),
        ]
    )
    text_signal = 0.8 * text[:, 0] - 0.45 * text[:, 1] + 0.25 * text[:, 2]
    fundamental_signal = (
        0.7 * fundamental[:, 0] + 0.5 * fundamental[:, 1] - 0.35 * fundamental[:, 3]
    )
    market_signal = 0.9 * market[:, 0] - 0.55 * market[:, 2] + 0.2 * market[:, 4]
    logits = np.column_stack(
        [
            1.1 * regime[:, 0] + 0.8 * regime[:, 2],
            -0.7 * regime[:, 0] - 0.25 * regime[:, 2],
            0.9 * regime[:, 1] + 0.15 * regime[:, 3],
        ]
    )
    logits -= logits.max(axis=1, keepdims=True)
    true_weights = np.exp(logits)
    true_weights /= true_weights.sum(axis=1, keepdims=True)
    target = 0.018 * (
        true_weights[:, 0] * text_signal
        + true_weights[:, 1] * fundamental_signal
        + true_weights[:, 2] * market_signal
    ) + generator.normal(0, 0.022, rows)

    for values, probability in ((text, 0.04), (fundamental, 0.08), (market, 0.025)):
        missing_rows = generator.random(rows) < probability
        values[missing_rows] = np.nan

    daily_dates = pd.bdate_range("2025-01-01", DEMO_AS_OF)
    market_returns = generator.normal(0.00025, 0.009, len(daily_dates))
    return_matrix = market_returns[:, None] * betas[None, :] + generator.normal(
        0, 0.011, size=(len(daily_dates), securities)
    )
    date_positions = {pd.Timestamp(day).normalize(): index for index, day in enumerate(daily_dates)}
    test_mask = pd.to_datetime(events["entry_date"]) >= pd.Timestamp("2025-01-01")
    for event_index in np.flatnonzero(test_mask.to_numpy()):
        security_index = int(events.iloc[event_index]["security_id"].split("-")[-1])
        start = date_positions.get(pd.Timestamp(events.iloc[event_index]["entry_date"]).normalize())
        if start is None:
            continue
        stop = min(start + 20, len(daily_dates))
        return_matrix[start:stop, security_index] += target[event_index] / max(stop - start, 1)
    daily_returns = pd.DataFrame(
        {
            "date": np.repeat(daily_dates.to_numpy(), securities),
            "security_id": np.tile(security_ids, len(daily_dates)),
            "return": return_matrix.reshape(-1),
        }
    )
    return SyntheticDataset(
        events=events,
        modalities={"text": text, "fundamental": fundamental, "market": market},
        regime=regime,
        target=target,
        daily_returns=daily_returns,
    )


def require_replaceable_demo_output(path: Path) -> None:
    """Refuse to replace anything except an earlier synthetic fixture.

    The committed public snapshot is authenticated, hash-locked evidence; a
    synthetic run must never overwrite it or any other non-synthetic file.
    """
    if not path.exists():
        return
    try:
        existing = orjson.loads(path.read_bytes())
    except (OSError, orjson.JSONDecodeError) as error:
        raise FileExistsError(f"Refusing to overwrite unreadable file {path}") from error
    metadata = existing.get("metadata") if isinstance(existing, dict) else None
    if not isinstance(metadata, dict) or metadata.get("data_mode") != "synthetic_fixture":
        raise FileExistsError(
            f"Refusing to overwrite non-synthetic snapshot {path}; "
            "write the synthetic demo to a separate path"
        )


def build_demo_snapshot(
    output: str | Path,
    config: ResearchConfig | None = None,
    seed: int = 42,
    max_epochs: int = 35,
) -> dict[str, Any]:
    """Train the full model on synthetic fixtures and export a recruiter-facing snapshot."""
    require_replaceable_demo_output(Path(output))
    if torch is None:
        raise RuntimeError(
            "The demo trains the real MoE. Install with `uv sync --extra research --extra dev`."
        )
    config = config or ResearchConfig()
    dataset = generate_synthetic_dataset(seed=seed)
    dates = pd.to_datetime(dataset.events["accepted_at"], utc=True)
    development = dates < pd.Timestamp("2023-01-01", tz="UTC")
    validation = (dates >= pd.Timestamp("2023-01-01", tz="UTC")) & (
        dates < pd.Timestamp("2025-01-01", tz="UTC")
    )
    test = dates >= pd.Timestamp("2025-01-01", tz="UTC")

    preprocessor = MultimodalPreprocessor()
    train_modalities, train_missing = preprocessor.fit_transform(
        {name: values[development] for name, values in dataset.modalities.items()}
    )
    validation_modalities, validation_missing = preprocessor.transform(
        {name: values[validation] for name, values in dataset.modalities.items()}
    )
    test_modalities, test_missing = preprocessor.transform(
        {name: values[test] for name, values in dataset.modalities.items()}
    )
    regime_scaler = StandardScaler().fit(dataset.regime[development])
    target_mean = float(dataset.target[development].mean())
    target_std = float(dataset.target[development].std()) or 1.0

    train_values = {
        **train_modalities,
        "regime": regime_scaler.transform(dataset.regime[development]).astype(np.float32),
        "missing_mask": train_missing,
        "target": ((dataset.target[development] - target_mean) / target_std).astype(np.float32),
    }
    validation_values = {
        **validation_modalities,
        "regime": regime_scaler.transform(dataset.regime[validation]).astype(np.float32),
        "missing_mask": validation_missing,
        "target": ((dataset.target[validation] - target_mean) / target_std).astype(np.float32),
    }
    test_values = {
        **test_modalities,
        "regime": regime_scaler.transform(dataset.regime[test]).astype(np.float32),
        "missing_mask": test_missing,
        "target": ((dataset.target[test] - target_mean) / target_std).astype(np.float32),
    }
    model = RegimeGatedMoE(
        text_dim=train_values["text"].shape[1],
        fundamental_dim=train_values["fundamental"].shape[1],
        market_dim=train_values["market"].shape[1],
        regime_dim=train_values["regime"].shape[1],
        hidden_dim=config.model.hidden_dim,
        expert_dim=config.model.expert_dim,
        dropout=config.model.dropout,
        gate_strength=config.model.gate_strength,
    )
    trained = train_moe(
        model,
        train_values,
        validation_values,
        learning_rate=config.model.learning_rate,
        weight_decay=config.model.weight_decay,
        entropy_regularization=config.model.entropy_regularization,
        expert_auxiliary_weight=config.model.expert_auxiliary_weight,
        correlation_regularization=config.model.correlation_regularization,
        batch_size=config.model.batch_size,
        max_epochs=max_epochs,
        patience=min(config.model.patience, 7),
        seed=seed,
        device="cpu",
    )
    validation_prediction = predict_moe(trained.model, validation_values, device="cpu")
    test_prediction = predict_moe(trained.model, test_values, device="cpu")
    validation_scores = validation_prediction.scores * target_std + target_mean
    test_scores = test_prediction.scores * target_std + target_mean
    validation_metrics = predictive_metrics(dataset.target[validation], validation_scores)
    test_horizon_dates = pd.to_datetime(dataset.events.loc[test, "exit_date"]).reset_index(
        drop=True
    )
    matured_test = (test_horizon_dates <= DEMO_AS_OF).to_numpy()
    test_targets = dataset.target[test]
    test_metrics = predictive_metrics(test_targets[matured_test], test_scores[matured_test])

    baseline_train = np.column_stack(
        [
            train_values["text"],
            train_values["fundamental"],
            train_values["market"],
            train_values["regime"],
        ]
    )
    baseline_validation = np.column_stack(
        [
            validation_values["text"],
            validation_values["fundamental"],
            validation_values["market"],
            validation_values["regime"],
        ]
    )
    baselines = train_baselines(
        baseline_train,
        dataset.target[development],
        baseline_validation,
        dataset.target[validation],
        seed,
    )

    test_events = dataset.events.loc[test].copy().reset_index(drop=True)
    test_events["score"] = test_scores
    test_events["rank"] = pd.Series(test_scores).rank(pct=True).to_numpy()
    test_events["realized_abnormal_return"] = np.where(matured_test, test_targets, np.nan)
    test_events["expert_weights"] = [
        {
            "text": float(weights[0]),
            "fundamental": float(weights[1]),
            "market": float(weights[2]),
        }
        for weights in test_prediction.expert_weights
    ]
    contributions = test_prediction.expert_weights * test_prediction.expert_predictions * target_std
    test_events["top_attributions"] = [
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
    backtest_signals = test_events[
        [
            "entry_date",
            "exit_date",
            "security_id",
            "ticker",
            "score",
            "beta",
            "industry_code",
        ]
    ]
    backtest = run_event_backtest(backtest_signals, dataset.daily_returns, config.portfolio)

    equity_curves: dict[str, list[dict[str, Any]]] = {}
    portfolio_scenarios: list[dict[str, Any]] = []
    for cost_bps in (10, 25, 50):
        scenario_daily = backtest.daily.copy()
        scenario_daily["net_return"] = (
            scenario_daily["gross_return"]
            - scenario_daily["turnover"] * cost_bps / 10_000
            - scenario_daily["borrow_cost"]
        )
        scenario_daily["equity"] = (1 + scenario_daily["net_return"]).cumprod()
        metrics = performance_metrics(scenario_daily)
        low, high = block_bootstrap_sharpe_interval(
            scenario_daily["net_return"].to_numpy(), samples=300, seed=seed
        )
        metrics["sharpe_ci_low"] = low
        metrics["sharpe_ci_high"] = high
        key = f"cost_{cost_bps}bps"
        equity_curves[key] = [
            {
                "date": pd.Timestamp(row.date).date().isoformat(),
                "equity": round(float(row.equity), 8),
                "drawdown": 0.0,
                "turnover": round(float(row.turnover), 8),
            }
            for row in scenario_daily.itertuples(index=False)
        ]
        running_peak = 0.0
        for point in equity_curves[key]:
            running_peak = max(running_peak, point["equity"])
            point["drawdown"] = round(point["equity"] / running_peak - 1, 8)
        portfolio_scenarios.append({"cost_bps": cost_bps, **_finite_mapping(metrics)})

    experiment_rows = [
        {
            "name": result.name,
            "family": "baseline",
            "validation_rmse": result.validation_rmse,
            "selected": False,
        }
        for result in baselines
    ]
    experiment_rows.append(
        {
            "name": "Regime-Gated MoE",
            "family": "multimodal",
            "validation_rmse": validation_metrics["rmse"],
            "validation_rank_ic": validation_metrics["rank_ic"],
            "selected": True,
            "best_epoch": trained.best_epoch,
        }
    )

    latest_cutoff = test_events["entry_date"].max() - pd.Timedelta(days=45)
    latest = test_events.loc[test_events["entry_date"] >= latest_cutoff].copy()
    latest = pd.concat([latest.nlargest(6, "score"), latest.nsmallest(6, "score")]).drop_duplicates(
        "event_id"
    )
    latest_records = [
        _event_record(row, pending=True)
        for _, row in latest.sort_values("score", ascending=False).iterrows()
    ]
    explorer = pd.concat(
        [
            test_events.nlargest(80, "score"),
            test_events.nsmallest(80, "score"),
            test_events.tail(80),
        ]
    ).drop_duplicates("event_id")
    event_records = [
        _event_record(row, pending=pd.Timestamp(row["exit_date"]) > DEMO_AS_OF)
        for _, row in explorer.sort_values("accepted_at", ascending=False).iterrows()
    ]

    snapshot: dict[str, Any] = {
        "metadata": {
            "project": "EDGAR-MoE",
            "version": "0.1.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "as_of": DEMO_AS_OF.date().isoformat(),
            "data_mode": "synthetic_fixture",
            "research_only": True,
            "disclaimer": "Synthetic fixture results for software verification only. Not investment advice.",
        },
        "summary": {
            "title": "Regime-Aware Multimodal Filing Alpha",
            "thesis": "The value of filing text, fundamentals, and market context varies across regimes; a learned gate should combine them more effectively than static fusion.",
            "universe": "Dynamic liquid US equities",
            "horizon_sessions": 20,
            "events": int(len(dataset.events)),
            "issuers": int(dataset.events["security_id"].nunique()),
            "development_events": int(development.sum()),
            "validation_events": int(validation.sum()),
            "test_events": int(test.sum()),
            "latest_signal_count": len(latest_records),
        },
        "predictive_metrics": {
            "validation": _finite_mapping(validation_metrics),
            "locked_test": _finite_mapping(test_metrics),
        },
        "portfolio_scenarios": portfolio_scenarios,
        "experiments": experiment_rows,
        "equity_curves": equity_curves,
        "events": event_records,
        "latest_signals": latest_records,
        "methodology": {
            "target": "20-session beta-adjusted abnormal return",
            "split": "Development 2018–2022; validation 2023–2024; locked test 2025–2026 with only matured labels scored",
            "model": "Frozen text representation + modality experts + regime-conditioned softmax gate",
            "portfolio": "Daily event-driven 50/50 long-short with beta, industry, and name constraints",
            "costs": "10 bps base transaction cost and 2% annual short borrow cost",
            "limitations": [
                "The bundled snapshot is intentionally synthetic and proves the software path, not alpha.",
                "Real research requires user-provided free API keys and a full point-in-time mapping audit.",
                "Historical borrow availability is unavailable in the free dataset and must be stress-tested.",
            ],
        },
        "freshness": {
            "status": "demo",
            "last_successful_update": datetime.now(UTC).isoformat(),
            "next_scheduled_update": None,
            "message": "Synthetic fixture snapshot; run the authenticated pipeline for real research data.",
        },
    }
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(orjson.dumps(snapshot, option=orjson.OPT_INDENT_2))
    return snapshot


def _event_record(row: pd.Series, pending: bool) -> dict[str, Any]:
    accepted_at = pd.Timestamp(row["accepted_at"])
    return {
        "accession_number": row["accession_number"],
        "event_id": row["event_id"],
        "security_id": row["security_id"],
        "ticker": row["ticker"],
        "company_name": row["company_name"],
        "form": row["form"],
        "accepted_at": accepted_at.isoformat(),
        "entry_date": pd.Timestamp(row["entry_date"]).date().isoformat(),
        "horizon_date": pd.Timestamp(row["exit_date"]).date().isoformat(),
        "industry_code": row["industry_code"],
        "score": round(float(row["score"]), 8),
        "rank": round(float(row["rank"]), 6),
        "direction": "long" if row["rank"] >= 0.9 else "short" if row["rank"] <= 0.1 else "neutral",
        "expert_weights": row["expert_weights"],
        "top_attributions": row["top_attributions"],
        "realized_abnormal_return": None
        if pending
        else round(float(row["realized_abnormal_return"]), 8),
        "filing_url": row["filing_url"],
    }


def _finite_mapping(values: dict[str, float]) -> dict[str, float | None]:
    return {key: float(value) if math.isfinite(value) else None for key, value in values.items()}
