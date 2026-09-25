from __future__ import annotations

import gzip
import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import orjson
import pandas as pd
import pandas_market_calendars as mcal
import polars as pl

from edgar_moe.data.refresh import verify_authenticated_bundle
from edgar_moe.data.sec import FilingParser, parse_sec_acceptance
from edgar_moe.data.storage import sha256_file, stable_json_hash
from edgar_moe.features.point_in_time import (
    assert_point_in_time,
    build_dynamic_universe,
    build_market_features,
)
from edgar_moe.features.tabular import build_fundamental_ratios
from edgar_moe.features.text import FinBertEmbedder, TextFeatures, filing_change_features
from edgar_moe.settings import LEGACY_XBRL_FACT_POLICY, ResearchConfig, XbrlFactPolicy

FUNDAMENTAL_CONCEPTS = (
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "CashAndCashEquivalentsAtCarryingValue",
    "AssetsCurrent",
    "LiabilitiesCurrent",
    "NetCashProvidedByUsedInOperatingActivities",
)
# Concepts measured over a reporting period rather than at an instant.
FLOW_CONCEPTS = frozenset(
    {
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "NetIncomeLoss",
        "OperatingIncomeLoss",
        "NetCashProvidedByUsedInOperatingActivities",
    }
)
# Revenue aliases compete as one "Revenues" input under duration_aware_v2.
REVENUE_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")
# duration_aware_v2 parameters (ADR 0015): facts must end within a quarter plus
# slack of the filing's period, and flows must span 60-380 days (12-week quarters
# through 53-week years) before being annualized.
MAXIMUM_FACT_AGE_DAYS = 120
MINIMUM_FLOW_DAYS = 60
MAXIMUM_FLOW_DAYS = 380
DAYS_PER_YEAR = 365.25
# Failed filing downloads accepted this recently may still have been tradable,
# so the prospective runner reports them instead of dropping them silently.
RECENT_DOWNLOAD_FAILURE_DAYS = 7


class TextEncoder(Protocol):
    @property
    def cache_identity(self) -> str: ...

    def encode(self, text: str) -> TextFeatures: ...


@dataclass(frozen=True)
class ResearchDataset:
    dataset_id: str
    as_of: date
    events: pd.DataFrame
    modalities: dict[str, np.ndarray]
    regime: np.ndarray
    target: np.ndarray
    daily_returns: pd.DataFrame
    availability: pd.DataFrame
    feature_names: dict[str, list[str]]
    attrition: dict[str, int]
    source_manifest_hash: str
    provenance: dict[str, str] = field(default_factory=dict)

    def save(self, output_root: str | Path) -> Path:
        """Write a new dataset identity, refusing to replace existing evidence.

        A repeated build must use a distinct dataset identity or be inspected
        explicitly; otherwise it could silently rewrite a frozen research input.
        """
        output = Path(output_root) / self.dataset_id
        output.mkdir(parents=True, exist_ok=False)
        events_path = output / "events.parquet"
        returns_path = output / "daily-returns.parquet"
        availability_path = output / "availability.parquet"
        arrays_path = output / "features.npz"
        _write_frame(events_path, self.events)
        _write_frame(returns_path, self.daily_returns)
        _write_frame(availability_path, self.availability)
        np.savez_compressed(
            arrays_path,
            text=self.modalities["text"],
            fundamental=self.modalities["fundamental"],
            market=self.modalities["market"],
            regime=self.regime,
            target=self.target,
        )
        assets = {
            "events": events_path,
            "daily_returns": returns_path,
            "availability": availability_path,
            "features": arrays_path,
        }
        manifest = {
            "dataset_id": self.dataset_id,
            "as_of": self.as_of.isoformat(),
            "source_manifest_hash": self.source_manifest_hash,
            "provenance": self.provenance,
            "row_counts": {
                "events": len(self.events),
                "matured_labels": int(np.isfinite(self.target).sum()),
                "daily_returns": len(self.daily_returns),
                "availability_records": len(self.availability),
            },
            "feature_names": self.feature_names,
            "attrition": self.attrition,
            "assets": {name: str(asset.relative_to(output)) for name, asset in assets.items()},
            "hashes": {name: sha256_file(asset) for name, asset in assets.items()},
        }
        _write_json(output / "manifest.json", manifest)
        return output

    @classmethod
    def load(cls, path: str | Path) -> ResearchDataset:
        root = Path(path)
        manifest = _read_json(root / "manifest.json")
        for name, expected_hash in manifest["hashes"].items():
            asset = (root / manifest["assets"][name]).resolve()
            if not asset.is_relative_to(root.resolve()):
                raise ValueError(f"Processed dataset asset escapes root: {name}")
            if sha256_file(asset) != expected_hash:
                raise ValueError(f"Processed dataset hash mismatch: {name}")
        arrays = np.load(root / manifest["assets"]["features"], allow_pickle=False)
        return cls(
            dataset_id=str(manifest["dataset_id"]),
            as_of=date.fromisoformat(str(manifest["as_of"])),
            events=_read_frame(root / manifest["assets"]["events"]),
            modalities={
                "text": arrays["text"],
                "fundamental": arrays["fundamental"],
                "market": arrays["market"],
            },
            regime=arrays["regime"],
            target=arrays["target"],
            daily_returns=_read_frame(root / manifest["assets"]["daily_returns"]),
            availability=_read_frame(root / manifest["assets"]["availability"]),
            feature_names={
                str(name): [str(value) for value in values]
                for name, values in manifest["feature_names"].items()
            },
            attrition={str(key): int(value) for key, value in manifest["attrition"].items()},
            source_manifest_hash=str(manifest["source_manifest_hash"]),
            provenance={
                str(key): str(value) for key, value in manifest.get("provenance", {}).items()
            },
        )


@dataclass(frozen=True)
class FactObservation:
    concept: str
    value: float
    end: date
    available_at: datetime
    accession_number: str
    # First day of the reporting period; None for instant (balance-sheet) facts.
    start: date | None = None


def dataset_xbrl_fact_policy(dataset: ResearchDataset) -> str:
    """Return the XBRL policy a dataset was built with; older datasets are legacy v1."""
    return dataset.provenance.get("xbrl_fact_policy", LEGACY_XBRL_FACT_POLICY)


def build_research_dataset(
    checkpoint: str | Path,
    *,
    config: ResearchConfig | None = None,
    embedder: TextEncoder | None = None,
    embedding_cache: str | Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> ResearchDataset:
    """Build the real point-in-time event matrix from an authenticated checkpoint."""
    config = config or ResearchConfig()
    checkpoint_root = Path(checkpoint)
    source_manifest = verify_authenticated_bundle(checkpoint_root)
    assets = source_manifest.configuration["assets"]
    as_of = date.fromisoformat(str(source_manifest.configuration["as_of"]))
    source_manifest_path = checkpoint_root / "manifest.json"
    source_manifest_hash = sha256_file(source_manifest_path)
    universe = _read_json(checkpoint_root / str(assets["universe"]))
    filing_index = _read_json(checkpoint_root / str(assets["filing_index"]))
    macro_payload = _read_json(checkpoint_root / str(assets["macro"]))
    bars = normalize_alpaca_bars(checkpoint_root / str(assets["bars"]))
    if bars.empty:
        raise ValueError("Authenticated checkpoint contains no market bars")

    schedule = _nyse_schedule(
        bars["date"].min().date(),
        as_of + timedelta(days=config.evaluation.horizon_sessions * 3 + 14),
    )
    market_features = build_market_features(
        bars[["date", "symbol", "close", "volume"]],
        momentum_windows=tuple(config.features.momentum_windows),
        volatility_windows=tuple(config.features.volatility_windows),
        beta_window=config.features.beta_window,
    )
    close_by_date = schedule.set_index("session_date")["market_close"]
    market_features["session_date"] = market_features["date"].dt.date
    market_features["available_at"] = market_features["session_date"].map(close_by_date)
    market_features = market_features.loc[market_features["available_at"].notna()].copy()
    dynamic_universe = build_dynamic_universe(
        market_features,
        universe_size=config.data.universe_size,
        minimum_price=config.data.minimum_price,
        minimum_history_sessions=config.data.minimum_history_sessions,
    )
    membership = {
        (str(row.month), str(row.symbol)) for row in dynamic_universe.itertuples(index=False)
    }

    member_by_cik = {str(item["cik"]).zfill(10): item for item in universe}
    security_by_symbol = {
        str(item["symbol"]): str(item.get("security_id") or f"alpaca:{item['symbol']}")
        for item in universe
    }
    market_groups = _indexed_market_groups(market_features)
    raw_bar_groups: dict[str, pd.DataFrame] = {}
    for symbol, group in bars.groupby("symbol", sort=False):
        ordered = group.sort_values("date").copy()
        ordered["session_date"] = ordered["date"].dt.date
        raw_bar_groups[str(symbol)] = ordered.set_index("session_date", drop=False)
    macro_groups = _indexed_macro_groups(_normalize_macro(macro_payload))
    acceptance_by_cik = _acceptance_maps(filing_index)
    fact_groups = _load_fact_groups(
        checkpoint_root,
        assets,
        member_by_cik,
        acceptance_by_cik,
    )

    text_encoder = embedder or FinBertEmbedder(
        model_name=config.features.embedding_model,
        chunk_tokens=config.features.embedding_chunk_tokens,
        max_chunks=config.features.embedding_max_chunks,
    )
    cache_directory = Path(embedding_cache) if embedding_cache is not None else None
    if cache_directory is not None:
        cache_directory.mkdir(parents=True, exist_ok=True)
    parser = FilingParser()
    previous_text: dict[tuple[str, str], TextFeatures] = {}

    market_columns = [
        *(f"momentum_{window}d" for window in config.features.momentum_windows),
        *(f"volatility_{window}d" for window in config.features.volatility_windows),
        "beta_252d",
        "median_dollar_volume_60d",
    ]
    regime_columns = [
        *sorted(macro_groups),
        "spy_momentum_21d",
        "spy_volatility_21d",
    ]
    fundamental_columns = list(build_fundamental_ratios(pd.DataFrame([{}])).columns)
    attrition = {
        "filing_records": len(filing_index),
        "download_failures": 0,
        "recent_download_failures": 0,
        "mapping_exclusions": 0,
        "universe_exclusions": 0,
        "missing_market_cutoff": 0,
        "text_parse_failures": 0,
        "included_events": 0,
        "immature_labels": 0,
    }
    event_rows: list[dict[str, Any]] = []
    text_rows: list[np.ndarray | None] = []
    fundamental_rows: list[dict[str, float]] = []
    market_rows: list[np.ndarray] = []
    regime_rows: list[np.ndarray] = []
    targets: list[float] = []
    availability_rows: list[dict[str, Any]] = []

    ordered_filings = sorted(
        filing_index, key=lambda item: str(item.get("acceptanceDateTime") or "")
    )
    text_cache_hits = 0
    text_cache_misses = 0

    def report_progress(processed: int) -> None:
        if progress is not None:
            progress(
                f"Dataset filings {processed:,}/{len(ordered_filings):,}; "
                f"included {attrition['included_events']:,}; "
                f"text cache hits {text_cache_hits:,}; encoded {text_cache_misses:,}"
            )

    report_progress(0)
    text_dimension: int | None = None
    for filing_number, filing in enumerate(ordered_filings, start=1):
        if filing_number > 1 and (filing_number - 1) % 250 == 0:
            report_progress(filing_number - 1)
        if filing.get("status") != "ok" or not filing.get("localPath"):
            attrition["download_failures"] += 1
            if _accepted_since(filing, as_of - timedelta(days=RECENT_DOWNLOAD_FAILURE_DAYS)):
                attrition["recent_download_failures"] += 1
            continue
        cik = str(filing.get("cik") or "").zfill(10)
        member = member_by_cik.get(cik)
        if member is None:
            attrition["mapping_exclusions"] += 1
            continue
        symbol = str(filing.get("symbol") or member["symbol"])
        security_id = str(
            filing.get("securityId") or member.get("security_id") or f"alpaca:{symbol}"
        )
        accepted_at = parse_sec_acceptance(str(filing["acceptanceDateTime"]))
        timing = _event_timing(accepted_at, schedule, config.evaluation.horizon_sessions)
        if timing is None:
            attrition["missing_market_cutoff"] += 1
            continue
        entry_at, horizon_at = timing
        selection_month = str(pd.Period(entry_at.date(), freq="M") - 1)
        if (selection_month, symbol) not in membership:
            attrition["universe_exclusions"] += 1
            continue
        market_row = _latest_market_row(market_groups.get(symbol), accepted_at)
        spy_row = _latest_market_row(market_groups.get("SPY"), accepted_at)
        if market_row is None or spy_row is None:
            attrition["missing_market_cutoff"] += 1
            continue

        accession = str(filing["accessionNumber"])
        report_period = date.fromisoformat(str(filing["reportDate"]))
        event_id = f"{cik}:{accession}"
        document_path = checkpoint_root / str(filing["localPath"])
        sections = parser.extract_sections(_read_filing_text(document_path))
        section_text = "\n\n".join(
            sections[name].text for name in config.features.text_sections if name in sections
        )
        text_vector: np.ndarray | None = None
        if section_text:
            current_text, cache_hit = _cached_text_features(
                text_encoder, section_text, cache_directory
            )
            if cache_hit:
                text_cache_hits += 1
            else:
                text_cache_misses += 1
            previous = previous_text.get((security_id, str(filing["form"])))
            changes = filing_change_features(current_text, previous)
            text_vector = np.concatenate(
                [
                    current_text.embedding,
                    current_text.sentiment,
                    np.asarray(list(changes.values()), dtype=np.float32),
                ]
            ).astype(np.float32)
            text_dimension = len(text_vector)
            previous_text[(security_id, str(filing["form"]))] = current_text
            availability_rows.extend(
                _availability_records(
                    event_id,
                    [f"text_{index}" for index in range(len(text_vector))],
                    accepted_at,
                    accepted_at,
                    "sec_filing_html",
                )
            )
        else:
            attrition["text_parse_failures"] += 1

        concept_values, fundamental_available_at = select_fundamentals(
            fact_groups.get(cik, {}),
            accepted_at,
            report_period,
            policy=config.features.xbrl_fact_policy,
        )
        availability_rows.extend(
            _availability_records(
                event_id,
                fundamental_columns,
                fundamental_available_at or accepted_at,
                accepted_at,
                "sec_companyfacts",
            )
        )

        market_vector = market_row[market_columns].to_numpy(dtype=np.float32)
        availability_rows.extend(
            _availability_records(
                event_id,
                market_columns,
                pd.Timestamp(market_row["available_at"]).to_pydatetime(),
                accepted_at,
                "alpaca_adjusted_bars",
            )
        )
        regime_vector, regime_availability = _regime_vector(
            macro_groups,
            accepted_at,
            spy_row,
            sorted(macro_groups),
        )
        for feature_name, available_at in zip(regime_columns, regime_availability, strict=True):
            availability_rows.append(
                {
                    "event_id": event_id,
                    "feature_name": feature_name,
                    "available_at": available_at,
                    "prediction_at": accepted_at,
                    "source": "macro_or_benchmark",
                }
            )

        beta = float(market_row.get("beta_252d", np.nan))
        target = _abnormal_return_target(
            raw_bar_groups,
            symbol,
            entry_at.date(),
            horizon_at.date(),
            beta,
            as_of,
        )
        if not math.isfinite(target):
            attrition["immature_labels"] += 1
        event_rows.append(
            {
                "event_id": event_id,
                "accession_number": accession,
                "cik": cik,
                "security_id": security_id,
                "ticker": symbol,
                "company_name": str(
                    filing.get("companyName") or member.get("company_name") or symbol
                ),
                "form": str(filing["form"]),
                "accepted_at": accepted_at,
                "report_period": report_period,
                "entry_at": entry_at,
                "entry_date": entry_at.date(),
                "horizon_at": horizon_at,
                "exit_date": horizon_at.date(),
                "industry_code": str(
                    filing.get("industryCode") or member.get("industry_code") or "unknown"
                ),
                "filing_url": str(filing["filingUrl"]),
                "beta": beta if math.isfinite(beta) else 1.0,
                "text_missing": text_vector is None,
                "fundamental_missing": not bool(concept_values),
                "market_missing": bool(np.isnan(market_vector).all()),
            }
        )
        text_rows.append(text_vector)
        fundamental_rows.append(concept_values)
        market_rows.append(market_vector)
        regime_rows.append(regime_vector)
        targets.append(target)
        attrition["included_events"] += 1

    report_progress(len(ordered_filings))

    if not event_rows:
        raise ValueError("No filing events survived the authenticated dataset build")
    if text_dimension is None:
        raise ValueError("No filing produced a usable configured text section")
    completed_text_rows = [
        row if row is not None else np.full(text_dimension, np.nan, dtype=np.float32)
        for row in text_rows
    ]
    availability = pd.DataFrame(availability_rows)
    assert_point_in_time(availability)
    events = pd.DataFrame(event_rows)
    modalities = {
        "text": np.vstack(completed_text_rows).astype(np.float32),
        "fundamental": build_fundamental_ratios(pd.DataFrame(fundamental_rows))
        .reindex(columns=fundamental_columns)
        .to_numpy(dtype=np.float32),
        "market": np.vstack(market_rows).astype(np.float32),
    }
    regime = np.vstack(regime_rows).astype(np.float32)
    target_values = np.asarray(targets, dtype=np.float32)
    daily_returns = build_daily_return_components(bars, security_by_symbol)
    dataset_identity = {
        "builder_version": 2,
        "source": source_manifest.dataset_id,
        # The source-system ID is date-scoped and is reused when a checkpoint
        # is refreshed. Include the verified manifest digest so a changed
        # checkpoint receives a new immutable identity instead of overwriting
        # a previously registered dataset with the same date/configuration.
        "source_manifest_hash": source_manifest_hash,
        "config": config.model_dump(),
        "text_encoder": text_encoder.cache_identity,
    }
    dataset_id = f"research-{as_of.isoformat()}-{stable_json_hash(dataset_identity)[:12]}"
    return ResearchDataset(
        dataset_id=dataset_id,
        as_of=as_of,
        events=events,
        modalities=modalities,
        regime=regime,
        target=target_values,
        daily_returns=daily_returns,
        availability=availability,
        feature_names={
            "text": [f"text_{index}" for index in range(text_dimension)],
            "fundamental": fundamental_columns,
            "market": market_columns,
            "regime": regime_columns,
        },
        attrition=attrition,
        source_manifest_hash=source_manifest_hash,
        provenance={
            "text_encoder": text_encoder.cache_identity,
            "market_adjustment": "split",
            "market_feed": str(source_manifest.configuration.get("feed", "unknown")),
            "xbrl_fact_policy": config.features.xbrl_fact_policy,
        },
    )


def normalize_alpaca_bars(path: str | Path) -> pd.DataFrame:
    """Normalize either the streaming NDJSON or legacy JSON bar checkpoint."""
    source = Path(path)
    records: list[dict[str, Any]] = []
    if source.suffix == ".ndjson":
        with source.open("rb") as stream:
            records = [orjson.loads(line) for line in stream if line.strip()]
    else:
        payload = _read_json(source)
        for symbol, rows in payload.items():
            records.extend({"symbol": symbol, **row} for row in rows)
    normalized: list[dict[str, Any]] = []
    for row in records:
        timestamp = row.get("t") or row.get("timestamp") or row.get("date")
        try:
            parsed = pd.Timestamp(timestamp)
        except (TypeError, ValueError):
            continue
        parsed = parsed.tz_localize("UTC") if parsed.tzinfo is None else parsed.tz_convert("UTC")
        normalized.append(
            {
                "date": parsed.normalize(),
                "symbol": str(row.get("symbol") or "").upper(),
                "open": _number(row.get("o", row.get("open"))),
                "high": _number(row.get("h", row.get("high"))),
                "low": _number(row.get("l", row.get("low"))),
                "close": _number(row.get("c", row.get("close"))),
                "volume": _number(row.get("v", row.get("volume"))),
                "trade_count": _number(row.get("n", row.get("trade_count"))),
                "vwap": _number(row.get("vw", row.get("vwap"))),
            }
        )
    frame = pd.DataFrame(normalized)
    if frame.empty:
        return frame
    frame = frame.loc[
        frame["symbol"].ne("") & frame["open"].gt(0) & frame["close"].gt(0) & frame["volume"].ge(0)
    ]
    return frame.drop_duplicates(["symbol", "date"], keep="last").sort_values(["symbol", "date"])


def build_daily_return_components(
    bars: pd.DataFrame, security_by_symbol: dict[str, str]
) -> pd.DataFrame:
    # SPY is fetched as a benchmark, but is not a filing issuer in the stock
    # universe. Preserve its returns for diagnostic abnormal-return calculations.
    security_by_symbol = {"SPY": "benchmark:SPY", **security_by_symbol}
    frame = bars.loc[bars["symbol"].isin(security_by_symbol)].copy()
    frame = frame.sort_values(["symbol", "date"])
    previous_close = frame.groupby("symbol", sort=False)["close"].shift(1)
    frame["return"] = frame["close"] / previous_close - 1
    frame["overnight_return"] = frame["open"] / previous_close - 1
    frame["intraday_return"] = frame["close"] / frame["open"] - 1
    frame["security_id"] = frame["symbol"].map(security_by_symbol)
    return frame[
        [
            "date",
            "security_id",
            "symbol",
            "return",
            "overnight_return",
            "intraday_return",
        ]
    ].dropna(subset=["return", "overnight_return", "intraday_return"])


def extract_company_facts(
    payload: dict[str, Any], acceptance_by_accession: dict[str, datetime]
) -> dict[str, list[FactObservation]]:
    result: dict[str, list[FactObservation]] = {}
    concepts = payload.get("facts", {}).get("us-gaap", {})
    for concept in FUNDAMENTAL_CONCEPTS:
        concept_payload = concepts.get(concept, {})
        units = concept_payload.get("units", {})
        unit_rows: list[dict[str, Any]] = []
        for preferred_unit in ("USD", "USD/shares", "shares", "pure"):
            if isinstance(units.get(preferred_unit), list):
                unit_rows = units[preferred_unit]
                break
        if not unit_rows:
            unit_rows = next((rows for rows in units.values() if isinstance(rows, list)), [])
        observations: list[FactObservation] = []
        for row in unit_rows:
            if row.get("form") not in {"10-K", "10-Q"}:
                continue
            try:
                value = float(row["val"])
                end = date.fromisoformat(str(row["end"]))
                accession = str(row.get("accn") or "")
                if accession in acceptance_by_accession:
                    available_at = acceptance_by_accession[accession]
                else:
                    filed = date.fromisoformat(str(row["filed"]))
                    available_at = datetime.combine(
                        filed + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                    )
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(value):
                # A malformed start date must not change which rows legacy v1 keeps.
                start = _optional_date(row.get("start"))
                observations.append(
                    FactObservation(concept, value, end, available_at, accession, start)
                )
        result[concept] = sorted(observations, key=lambda item: (item.available_at, item.end))
    return result


def select_fundamentals(
    facts: dict[str, list[FactObservation]],
    cutoff: datetime,
    report_period: date,
    *,
    policy: XbrlFactPolicy,
) -> tuple[dict[str, float], datetime | None]:
    """Choose the XBRL values behind one filing's fundamental ratios."""
    if policy == LEGACY_XBRL_FACT_POLICY:
        return _legacy_v1_fundamentals(facts, cutoff, report_period)
    if policy == "duration_aware_v2":
        return _duration_aware_fundamentals(facts, cutoff, report_period)
    raise ValueError(f"Unsupported XBRL fact policy: {policy}")


def _legacy_v1_fundamentals(
    facts: dict[str, list[FactObservation]],
    cutoff: datetime,
    report_period: date,
) -> tuple[dict[str, float], datetime | None]:
    """Reproduce the frozen v1 selection exactly.

    Known defects, retained only so v1 features stay identical (ADR 0015):
    flow concepts mix quarterly and year-to-date periods, and a discontinued
    ``Revenues`` concept outranks a current contract-revenue fact however old
    it is.
    """
    values, available_at = _latest_fundamentals(facts, cutoff, report_period)
    replacement = values.get("RevenueFromContractWithCustomerExcludingAssessedTax")
    if "Revenues" not in values and replacement is not None:
        values["Revenues"] = replacement
    return values, available_at


def _duration_aware_fundamentals(
    facts: dict[str, list[FactObservation]],
    cutoff: datetime,
    report_period: date,
) -> tuple[dict[str, float], datetime | None]:
    """Select current, comparable facts (duration_aware_v2, ADR 0015).

    Facts must be available by the cutoff and end within
    ``MAXIMUM_FACT_AGE_DAYS`` of the filing's report period. Flow concepts also
    need an explicit reporting period: the latest period end wins, the shortest
    eligible period is preferred (the quarter over year-to-date), and the value
    is annualized so quarterly, year-to-date, and annual facts share one scale.
    """
    earliest_end = report_period - timedelta(days=MAXIMUM_FACT_AGE_DAYS)
    groups = {
        concept: facts.get(concept, [])
        for concept in FUNDAMENTAL_CONCEPTS
        if concept not in REVENUE_CONCEPTS
    }
    groups["Revenues"] = [item for concept in REVENUE_CONCEPTS for item in facts.get(concept, [])]
    values: dict[str, float] = {}
    available_times: list[datetime] = []
    for concept, observations in groups.items():
        flow = concept in FLOW_CONCEPTS
        eligible = [
            item
            for item in observations
            if item.available_at <= cutoff
            and earliest_end <= item.end <= report_period
            and (not flow or _flow_days(item) is not None)
        ]
        if not eligible:
            continue
        chosen = max(
            eligible,
            key=lambda item: (
                item.end,
                -(_flow_days(item) or 0),
                item.available_at,
                # Total revenue outranks contract revenue for the same period.
                item.concept == "Revenues",
            ),
        )
        days = _flow_days(chosen)
        values[concept] = chosen.value * DAYS_PER_YEAR / days if flow and days else chosen.value
        available_times.append(chosen.available_at)
    return values, max(available_times, default=None)


def _flow_days(item: FactObservation) -> int | None:
    """Return the inclusive length of a usable flow period, or None."""
    if item.start is None:
        return None
    days = (item.end - item.start).days + 1
    return days if MINIMUM_FLOW_DAYS <= days <= MAXIMUM_FLOW_DAYS else None


def _accepted_since(filing: dict[str, Any], earliest: date) -> bool:
    try:
        accepted_at = parse_sec_acceptance(str(filing["acceptanceDateTime"]))
    except (KeyError, TypeError, ValueError):
        return False
    return accepted_at.date() >= earliest


def _optional_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _latest_fundamentals(
    facts: dict[str, list[FactObservation]],
    cutoff: datetime,
    report_period: date,
) -> tuple[dict[str, float], datetime | None]:
    values: dict[str, float] = {}
    available_times: list[datetime] = []
    for concept, observations in facts.items():
        eligible = [
            item
            for item in observations
            if item.available_at <= cutoff and item.end <= report_period
        ]
        if not eligible:
            continue
        latest = max(eligible, key=lambda item: (item.end, item.available_at))
        values[concept] = latest.value
        available_times.append(latest.available_at)
    return values, max(available_times, default=None)


def _acceptance_maps(
    filing_index: list[dict[str, Any]],
) -> dict[str, dict[str, datetime]]:
    result: dict[str, dict[str, datetime]] = {}
    for row in filing_index:
        try:
            cik = str(row["cik"]).zfill(10)
            accession = str(row["accessionNumber"])
            result.setdefault(cik, {})[accession] = parse_sec_acceptance(
                str(row["acceptanceDateTime"])
            )
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _load_fact_groups(
    checkpoint_root: Path,
    assets: dict[str, Any],
    member_by_cik: dict[str, dict[str, Any]],
    acceptance_by_cik: dict[str, dict[str, datetime]],
) -> dict[str, dict[str, list[FactObservation]]]:
    result: dict[str, dict[str, list[FactObservation]]] = {}
    for cik in member_by_cik:
        asset_name = f"companyfacts_{cik}"
        relative = assets.get(asset_name)
        if relative is None:
            continue
        result[cik] = extract_company_facts(
            _read_json(checkpoint_root / str(relative)), acceptance_by_cik.get(cik, {})
        )
    return result


def _normalize_macro(payload: dict[str, Any]) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for series, rows in payload.items():
        normalized: list[dict[str, Any]] = []
        for row in rows:
            try:
                observation_date = date.fromisoformat(str(row["date"]))
                release_date = date.fromisoformat(str(row["realtime_start"]))
                value = float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            normalized.append(
                {
                    "observation_date": observation_date,
                    "available_at": datetime.combine(
                        release_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                    ),
                    "value": value,
                }
            )
        result[str(series)] = (
            pd.DataFrame(normalized).sort_values(["available_at", "observation_date"])
            if normalized
            else pd.DataFrame(columns=["observation_date", "available_at", "value"])
        )
    return result


def _regime_vector(
    macro_groups: dict[str, tuple[np.ndarray, pd.DataFrame]],
    cutoff: datetime,
    spy_row: pd.Series,
    series_names: list[str],
) -> tuple[np.ndarray, list[datetime]]:
    values: list[float] = []
    availability: list[datetime] = []
    cutoff_value = pd.Timestamp(cutoff).value
    for series in series_names:
        timestamps, rows = macro_groups[series]
        index = int(np.searchsorted(timestamps, cutoff_value, side="right") - 1)
        if index < 0:
            values.append(float("nan"))
            availability.append(cutoff)
        else:
            latest = rows.iloc[index]
            values.append(float(latest["value"]))
            availability.append(pd.Timestamp(latest["available_at"]).to_pydatetime())
    for column in ("momentum_21d", "volatility_21d"):
        values.append(float(spy_row.get(column, np.nan)))
        availability.append(pd.Timestamp(spy_row["available_at"]).to_pydatetime())
    return np.asarray(values, dtype=np.float32), availability


def _nanosecond_timestamps(values: pd.Series) -> np.ndarray:
    """Use the same epoch unit as ``Timestamp.value`` across pandas versions."""
    return np.asarray(
        pd.to_datetime(values, utc=True).dt.as_unit("ns").astype("int64").to_numpy(),
        dtype=np.int64,
    )


def _indexed_market_groups(
    market_features: pd.DataFrame,
) -> dict[str, tuple[np.ndarray, pd.DataFrame]]:
    result: dict[str, tuple[np.ndarray, pd.DataFrame]] = {}
    for symbol, group in market_features.groupby("symbol", sort=False):
        ordered = group.sort_values("available_at").reset_index(drop=True)
        timestamps = _nanosecond_timestamps(ordered["available_at"])
        result[str(symbol)] = timestamps, ordered
    return result


def _indexed_macro_groups(
    macro_groups: dict[str, pd.DataFrame],
) -> dict[str, tuple[np.ndarray, pd.DataFrame]]:
    result: dict[str, tuple[np.ndarray, pd.DataFrame]] = {}
    for series, frame in macro_groups.items():
        ordered = frame.sort_values("available_at").reset_index(drop=True)
        timestamps = _nanosecond_timestamps(ordered["available_at"])
        result[series] = timestamps, ordered
    return result


def _latest_market_row(
    group: tuple[np.ndarray, pd.DataFrame] | None, cutoff: datetime
) -> pd.Series | None:
    if group is None:
        return None
    timestamps, frame = group
    cutoff_value = pd.Timestamp(cutoff).value
    index = int(np.searchsorted(timestamps, cutoff_value, side="right") - 1)
    return None if index < 0 else frame.iloc[index]


def _nyse_schedule(start: date, end: date) -> pd.DataFrame:
    schedule = mcal.get_calendar("NYSE").schedule(start_date=start, end_date=end).reset_index()
    schedule = schedule.rename(columns={schedule.columns[0]: "session"})
    schedule["session_date"] = pd.to_datetime(schedule["session"]).dt.date
    schedule["market_open"] = pd.to_datetime(schedule["market_open"], utc=True)
    schedule["market_close"] = pd.to_datetime(schedule["market_close"], utc=True)
    return schedule[["session_date", "market_open", "market_close"]]


def _event_timing(
    accepted_at: datetime, schedule: pd.DataFrame, horizon_sessions: int
) -> tuple[datetime, datetime] | None:
    opens = _nanosecond_timestamps(schedule["market_open"])
    entry_index = int(np.searchsorted(opens, pd.Timestamp(accepted_at).value, side="right"))
    horizon_index = entry_index + horizon_sessions - 1
    if entry_index >= len(schedule) or horizon_index >= len(schedule):
        return None
    entry = pd.Timestamp(schedule.iloc[entry_index]["market_open"]).to_pydatetime()
    horizon = pd.Timestamp(schedule.iloc[horizon_index]["market_close"]).to_pydatetime()
    return entry, horizon


def _abnormal_return_target(
    raw_bar_groups: dict[str, pd.DataFrame],
    symbol: str,
    entry_date: date,
    horizon_date: date,
    beta: float,
    as_of: date,
) -> float:
    if horizon_date > as_of or symbol not in raw_bar_groups or "SPY" not in raw_bar_groups:
        return float("nan")
    asset = raw_bar_groups[symbol]
    benchmark = raw_bar_groups["SPY"]
    if (
        entry_date not in asset.index
        or horizon_date not in asset.index
        or entry_date not in benchmark.index
        or horizon_date not in benchmark.index
    ):
        return float("nan")
    asset_return = float(asset.at[horizon_date, "close"] / asset.at[entry_date, "open"] - 1)
    benchmark_return = float(
        benchmark.at[horizon_date, "close"] / benchmark.at[entry_date, "open"] - 1
    )
    beta_value = beta if math.isfinite(beta) else 1.0
    return asset_return - beta_value * benchmark_return


def _cached_text_features(
    embedder: TextEncoder, text: str, cache_directory: Path | None
) -> tuple[TextFeatures, bool]:
    identity = embedder.cache_identity
    cache_key = hashlib.sha256(f"{identity}\0{text}".encode()).hexdigest()
    if cache_directory is None:
        return embedder.encode(text), False
    cache_path = cache_directory / f"{cache_key}.npz"
    if cache_path.exists():
        with np.load(cache_path, allow_pickle=False) as values:
            return (
                TextFeatures(
                    embedding=values["embedding"],
                    sentiment=values["sentiment"],
                    token_count=int(values["token_count"]),
                ),
                True,
            )
    features = embedder.encode(text)
    temporary = cache_path.with_suffix(".npz.tmp")
    with temporary.open("wb") as stream:
        np.savez_compressed(
            stream,
            embedding=features.embedding,
            sentiment=features.sentiment,
            token_count=np.asarray(features.token_count),
        )
    temporary.replace(cache_path)
    return features, False


def _availability_records(
    event_id: str,
    feature_names: list[str],
    available_at: datetime,
    prediction_at: datetime,
    source: str,
) -> list[dict[str, Any]]:
    return [
        {
            "event_id": event_id,
            "feature_name": feature_name,
            "available_at": available_at,
            "prediction_at": prediction_at,
            "source": source,
        }
        for feature_name in feature_names
    ]


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _read_filing_text(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as stream:
            return stream.read()
    return path.read_text(encoding="utf-8", errors="replace")


def _write_frame(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    pl.DataFrame(frame.to_dict(orient="list")).write_parquet(
        temporary, compression="zstd", statistics=True
    )
    temporary.replace(path)


def _read_frame(path: Path) -> pd.DataFrame:
    return pd.DataFrame(pl.read_parquet(path).to_dict(as_series=False))


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    temporary.replace(path)


def _read_json(path: Path) -> Any:
    payload = path.read_bytes()
    return orjson.loads(gzip.decompress(payload) if path.suffix == ".gz" else payload)
