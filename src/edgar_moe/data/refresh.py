from __future__ import annotations

import asyncio
import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

import orjson
from pydantic import BaseModel, ConfigDict, Field, field_validator

from edgar_moe.data.contracts import DatasetManifest
from edgar_moe.data.storage import sha256_file


class UniverseMember(BaseModel):
    """Explicit point-in-time research-universe input with no hidden symbol mapping."""

    model_config = ConfigDict(frozen=True)

    cik: str = Field(pattern=r"^\d{10}$")
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,14}$")
    security_id: str | None = None
    company_name: str | None = None

    @field_validator("cik", mode="before")
    @classmethod
    def normalize_cik(cls, value: object) -> str:
        return str(value).strip().zfill(10)

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, value: object) -> str:
        return str(value).strip().upper()


class SecSource(Protocol):
    async def submissions(self, cik: str) -> dict[str, Any]: ...

    async def company_facts(self, cik: str) -> dict[str, Any]: ...


class MarketSource(Protocol):
    async def daily_bars(
        self, symbols: list[str], start: date, end: date, feed: str = "iex"
    ) -> dict[str, list[dict[str, Any]]]: ...


class MacroSource(Protocol):
    async def observations_as_known(
        self,
        series_id: str,
        observation_start: str,
        observation_end: str,
        as_of: str,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class AuthenticatedRefreshBundle:
    universe: list[dict[str, Any]]
    submissions: dict[str, dict[str, Any]]
    company_facts: dict[str, dict[str, Any]]
    bars: dict[str, list[dict[str, Any]]]
    macro: dict[str, list[dict[str, Any]]]
    start: date
    end: date
    as_of: date
    feed: str


def load_universe_csv(path: str | Path) -> list[UniverseMember]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Universe file not found: {source}")
    with source.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("Universe file must contain at least one member")
    if not {"cik", "symbol"}.issubset(rows[0]):
        raise ValueError("Universe CSV must contain cik and symbol columns")
    members = [UniverseMember.model_validate(row) for row in rows]
    symbols = [member.symbol for member in members]
    ciks = [member.cik for member in members]
    if len(symbols) != len(set(symbols)) or len(ciks) != len(set(ciks)):
        raise ValueError("Universe members must have unique CIKs and symbols")
    return members


async def collect_authenticated_data(
    sec: SecSource,
    market: MarketSource,
    macro: MacroSource,
    members: Sequence[UniverseMember],
    *,
    start: date,
    end: date,
    as_of: date,
    macro_series: Sequence[str],
    feed: str = "iex",
    market_batch_size: int = 200,
) -> AuthenticatedRefreshBundle:
    """Collect raw research inputs while preserving the requested vintage cutoff."""
    if not members:
        raise ValueError("At least one universe member is required")
    if not start <= end <= as_of:
        raise ValueError("Expected start <= end <= as_of")
    if market_batch_size < 1:
        raise ValueError("market_batch_size must be positive")

    sec_calls = [
        call
        for member in members
        for call in (sec.submissions(member.cik), sec.company_facts(member.cik))
    ]
    sec_payloads = await asyncio.gather(*sec_calls)
    submissions = {
        member.cik: sec_payloads[index * 2] for index, member in enumerate(members)
    }
    company_facts = {
        member.cik: sec_payloads[index * 2 + 1] for index, member in enumerate(members)
    }

    symbols = sorted({member.symbol for member in members} | {"SPY"})
    bars: dict[str, list[dict[str, Any]]] = {}
    for offset in range(0, len(symbols), market_batch_size):
        payload = await market.daily_bars(
            symbols[offset : offset + market_batch_size], start, end, feed=feed
        )
        for symbol, rows in payload.items():
            bars.setdefault(symbol, []).extend(rows)

    normalized_series = sorted({series.strip().upper() for series in macro_series if series.strip()})
    macro_payloads = await asyncio.gather(
        *[
            macro.observations_as_known(
                series,
                start.isoformat(),
                end.isoformat(),
                as_of.isoformat(),
            )
            for series in normalized_series
        ]
    )
    macro_values = dict(zip(normalized_series, macro_payloads, strict=True))
    return AuthenticatedRefreshBundle(
        universe=[member.model_dump() for member in members],
        submissions=submissions,
        company_facts=company_facts,
        bars=bars,
        macro=macro_values,
        start=start,
        end=end,
        as_of=as_of,
        feed=feed,
    )


def write_authenticated_bundle(
    bundle: AuthenticatedRefreshBundle, output_root: str | Path
) -> Path:
    """Write an immutable, hash-addressed input bundle without storing credentials."""
    run_directory = Path(output_root) / bundle.as_of.isoformat()
    sec_directory = run_directory / "sec"
    market_directory = run_directory / "market"
    macro_directory = run_directory / "macro"
    for directory in (sec_directory, market_directory, macro_directory):
        directory.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}
    paths["universe"] = _write_json(run_directory / "universe.json", bundle.universe)
    for cik, payload in bundle.submissions.items():
        paths[f"submissions_{cik}"] = _write_json(
            sec_directory / f"CIK{cik}-submissions.json", payload
        )
    for cik, payload in bundle.company_facts.items():
        paths[f"companyfacts_{cik}"] = _write_json(
            sec_directory / f"CIK{cik}-companyfacts.json", payload
        )
    paths["bars"] = _write_json(market_directory / "daily-bars.json", bundle.bars)
    paths["macro"] = _write_json(macro_directory / "observations.json", bundle.macro)

    recent_filings = sum(
        len(payload.get("filings", {}).get("recent", {}).get("accessionNumber", []))
        for payload in bundle.submissions.values()
    )
    fact_concepts = sum(
        len(payload.get("facts", {}).get("us-gaap", {}))
        for payload in bundle.company_facts.values()
    )
    manifest = DatasetManifest(
        dataset_id=f"authenticated-{bundle.as_of.isoformat()}",
        created_at=datetime.now(UTC),
        source_versions={
            "sec": "data.sec.gov submissions + companyfacts",
            "market": f"Alpaca adjusted daily bars ({bundle.feed})",
            "macro": f"FRED vintage as known on {bundle.as_of.isoformat()}",
        },
        row_counts={
            "universe_members": len(bundle.universe),
            "recent_filing_records": recent_filings,
            "xbrl_concepts": fact_concepts,
            "daily_bars": sum(len(rows) for rows in bundle.bars.values()),
            "macro_observations": sum(len(rows) for rows in bundle.macro.values()),
        },
        hashes={name: sha256_file(path) for name, path in sorted(paths.items())},
        configuration={
            "start": bundle.start.isoformat(),
            "end": bundle.end.isoformat(),
            "as_of": bundle.as_of.isoformat(),
            "feed": bundle.feed,
            "macro_series": sorted(bundle.macro),
        },
    )
    _write_json(run_directory / "manifest.json", manifest.model_dump(mode="json"))
    return run_directory


def _write_json(path: Path, payload: Any) -> Path:
    path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    return path
