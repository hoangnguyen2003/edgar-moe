from __future__ import annotations

import asyncio
import csv
import gzip
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

import orjson
from pydantic import BaseModel, ConfigDict, Field, field_validator

from edgar_moe.data.contracts import DatasetManifest
from edgar_moe.data.sec import periodic_filing_records
from edgar_moe.data.storage import sha256_file


class UniverseMember(BaseModel):
    """Explicit point-in-time research-universe input with no hidden symbol mapping."""

    model_config = ConfigDict(frozen=True)

    cik: str = Field(pattern=r"^\d{10}$")
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,14}$")
    security_id: str | None = None
    company_name: str | None = None
    exchange: str | None = None
    industry_code: str | None = None

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


class StreamingSecSource(SecSource, Protocol):
    async def complete_submissions(self, cik: str, start: date | None = None) -> dict[str, Any]: ...

    async def filing_html(self, cik: str, accession: str, primary_document: str) -> str: ...


class MarketSource(Protocol):
    async def daily_bars(
        self, symbols: list[str], start: date, end: date, feed: str = "iex"
    ) -> dict[str, list[dict[str, Any]]]: ...


class StreamingMarketSource(MarketSource, Protocol):
    async def corporate_actions(
        self, start: date, end: date, symbols: list[str] | None = None
    ) -> dict[str, Any]: ...


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

    complete_submissions = getattr(sec, "complete_submissions", None)
    sec_calls = []
    for member in members:
        if complete_submissions is None:
            sec_calls.append(sec.submissions(member.cik))
        else:
            sec_calls.append(complete_submissions(member.cik, start))
        sec_calls.append(sec.company_facts(member.cik))
    sec_payloads = await asyncio.gather(*sec_calls)
    submissions = {member.cik: sec_payloads[index * 2] for index, member in enumerate(members)}
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

    normalized_series = sorted(
        {series.strip().upper() for series in macro_series if series.strip()}
    )
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


def write_authenticated_bundle(bundle: AuthenticatedRefreshBundle, output_root: str | Path) -> Path:
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
            "macro": "FRED/ALFRED initial-release observations with availability dates",
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
            "assets": {
                name: str(path.relative_to(run_directory)) for name, path in sorted(paths.items())
            },
        },
    )
    _write_json(run_directory / "manifest.json", manifest.model_dump(mode="json"))
    return run_directory


async def refresh_authenticated_to_disk(
    sec: StreamingSecSource,
    market: StreamingMarketSource,
    macro: MacroSource,
    members: Sequence[UniverseMember],
    *,
    output_root: str | Path,
    start: date,
    end: date,
    as_of: date,
    macro_series: Sequence[str],
    forms: Sequence[str] = ("10-K", "10-Q"),
    feed: str = "iex",
    market_batch_size: int = 200,
    maximum_filings_per_issuer: int | None = None,
    resume: bool = False,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Stream a resumable authenticated input checkpoint directly to disk."""
    if not members:
        raise ValueError("At least one universe member is required")
    if not start <= end <= as_of:
        raise ValueError("Expected start <= end <= as_of")
    if market_batch_size < 1:
        raise ValueError("market_batch_size must be positive")
    if maximum_filings_per_issuer is not None and maximum_filings_per_issuer < 1:
        raise ValueError("maximum_filings_per_issuer must be positive")

    run_directory = Path(output_root) / as_of.isoformat()
    manifest_path = run_directory / "manifest.json"
    normalized_series = sorted(
        {series.strip().upper() for series in macro_series if series.strip()}
    )
    requested_universe = [member.model_dump(mode="json") for member in members]
    request = {
        "schema_version": 1,
        "universe": requested_universe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "as_of": as_of.isoformat(),
        "feed": feed,
        "forms": list(forms),
        "macro_series": normalized_series,
        "market_batch_size": market_batch_size,
        "maximum_filings_per_issuer": maximum_filings_per_issuer,
    }
    request_path = run_directory / "request.json"
    if manifest_path.exists():
        if not resume:
            raise FileExistsError(
                f"Authenticated checkpoint already exists at {run_directory}; use resume=True"
            )
        manifest = verify_authenticated_bundle(run_directory)
        _assert_completed_request(run_directory, manifest, request)
        return run_directory  # A finalized checkpoint is immutable, even on resume.
    if run_directory.exists() and any(run_directory.iterdir()):
        if not resume:
            raise FileExistsError(
                f"Interrupted checkpoint already exists at {run_directory}; use resume=True"
            )
        if not request_path.exists():
            raise ValueError(
                "Interrupted checkpoint has no request contract; start a new checkpoint"
            )
        if _read_json(request_path) != request:
            raise ValueError("Resume request differs from the checkpoint's original request")
    else:
        _write_json(request_path, request)

    sec_directory = run_directory / "sec"
    filing_directory = sec_directory / "filings"
    market_directory = run_directory / "market"
    macro_directory = run_directory / "macro"
    for directory in (sec_directory, filing_directory, market_directory, macro_directory):
        directory.mkdir(parents=True, exist_ok=True)

    asset_paths: dict[str, Path] = {}
    requested_universe_path = run_directory / "requested-universe.json"
    _write_json(requested_universe_path, requested_universe)
    asset_paths["requested_universe"] = requested_universe_path
    asset_paths["request"] = request_path

    filing_index: list[dict[str, Any]] = []
    research_members: list[UniverseMember] = []
    filing_record_count = 0
    xbrl_concept_count = 0
    for member_index, member in enumerate(members, start=1):
        submissions_path = sec_directory / f"CIK{member.cik}-submissions.json.gz"
        facts_path = sec_directory / f"CIK{member.cik}-companyfacts.json.gz"
        if resume and _has_verified_sidecar(submissions_path):
            submissions = _read_json(submissions_path)
        else:
            submissions = await sec.complete_submissions(member.cik, start)
            _write_json(submissions_path, submissions)
            _write_sidecar_hash(submissions_path)
        asset_paths[f"submissions_{member.cik}"] = submissions_path
        records = periodic_filing_records(
            submissions,
            start=start,
            end=end,
            forms=tuple(forms),
            maximum=maximum_filings_per_issuer,
        )
        if not records:
            if progress and (member_index % 10 == 0 or member_index == len(members)):
                progress(
                    f"SEC issuers {member_index:,}/{len(members):,}; "
                    f"eligible {len(research_members):,}; filings {filing_record_count:,}"
                )
            continue
        research_member = member.model_copy(
            update={
                "company_name": member.company_name
                or str(submissions.get("name") or member.symbol),
                "industry_code": member.industry_code or str(submissions.get("sic") or "unknown"),
            }
        )
        research_members.append(research_member)
        if resume and _has_verified_sidecar(facts_path):
            facts = _read_json(facts_path)
        else:
            facts = await sec.company_facts(member.cik)
            _write_json(facts_path, facts)
            _write_sidecar_hash(facts_path)
        asset_paths[f"companyfacts_{member.cik}"] = facts_path
        xbrl_concept_count += len(facts.get("facts", {}).get("us-gaap", {}))
        filing_record_count += len(records)
        issuer_directory = filing_directory / f"CIK{member.cik}"
        issuer_directory.mkdir(parents=True, exist_ok=True)
        filing_index.extend(
            await asyncio.gather(
                *[
                    _download_filing_record(
                        sec,
                        research_member,
                        record,
                        issuer_directory=issuer_directory,
                        run_directory=run_directory,
                        resume=resume,
                    )
                    for record in records
                ]
            )
        )
        if progress and (member_index % 10 == 0 or member_index == len(members)):
            progress(
                f"SEC issuers {member_index:,}/{len(members):,}; "
                f"eligible {len(research_members):,}; filings {filing_record_count:,}"
            )

    if not research_members:
        raise ValueError("No requested issuer had an eligible periodic filing")
    universe_path = run_directory / "universe.json"
    _write_json(
        universe_path,
        [member.model_dump(mode="json") for member in research_members],
    )
    asset_paths["universe"] = universe_path

    filing_index_path = sec_directory / "filing-index.json"
    _write_json(filing_index_path, filing_index)
    asset_paths["filing_index"] = filing_index_path

    symbols = sorted({member.symbol for member in research_members} | {"SPY"})
    bars_path = market_directory / "daily-bars.ndjson"
    if not (resume and _has_verified_sidecar(bars_path)):
        temporary_bars = bars_path.with_suffix(".ndjson.tmp")
        with temporary_bars.open("wb") as stream:
            for offset in range(0, len(symbols), market_batch_size):
                batch = symbols[offset : offset + market_batch_size]
                payload = await market.daily_bars(batch, start, end, feed=feed)
                for symbol, rows in sorted(payload.items()):
                    for row in rows:
                        stream.write(
                            orjson.dumps(
                                {"symbol": symbol, **row},
                                option=orjson.OPT_SERIALIZE_NUMPY,
                            )
                        )
                        stream.write(b"\n")
                if progress:
                    progress(
                        f"Market bars {min(offset + market_batch_size, len(symbols)):,}/"
                        f"{len(symbols):,} symbols"
                    )
        temporary_bars.replace(bars_path)
        _write_sidecar_hash(bars_path)
    asset_paths["bars"] = bars_path
    daily_bar_count = _count_nonempty_lines(bars_path)

    actions_path = market_directory / "corporate-actions.json.gz"
    corporate_actions: dict[str, Any]
    if resume and _has_verified_sidecar(actions_path):
        corporate_actions = _read_json(actions_path)
    else:
        corporate_actions = {}
        for offset in range(0, len(symbols), market_batch_size):
            payload = await market.corporate_actions(
                start, end, symbols[offset : offset + market_batch_size]
            )
            _merge_list_payload(corporate_actions, payload)
            if progress:
                progress(
                    f"Corporate actions "
                    f"{min(offset + market_batch_size, len(symbols)):,}/{len(symbols):,} symbols"
                )
        _write_json(actions_path, corporate_actions)
        _write_sidecar_hash(actions_path)
    asset_paths["corporate_actions"] = actions_path

    macro_path = macro_directory / "observations.json.gz"
    if resume and _has_verified_sidecar(macro_path):
        macro_values = _read_json(macro_path)
    else:
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
        _write_json(macro_path, macro_values)
        _write_sidecar_hash(macro_path)
    if progress:
        progress(f"Macro series {len(normalized_series):,}/{len(normalized_series):,}")
    asset_paths["macro"] = macro_path

    successful_documents = sum(item["status"] == "ok" for item in filing_index)
    corporate_action_count = _count_nested_lists(corporate_actions)
    manifest = DatasetManifest(
        dataset_id=f"authenticated-{as_of.isoformat()}",
        created_at=datetime.now(UTC),
        source_versions={
            "sec": "data.sec.gov complete submissions + companyfacts + filing HTML",
            "market": f"Alpaca split-adjusted daily bars and corporate actions ({feed})",
            "macro": "FRED/ALFRED initial-release observations with availability dates",
        },
        row_counts={
            "requested_universe_members": len(members),
            "universe_members": len(research_members),
            "ineligible_form_issuers": len(members) - len(research_members),
            "periodic_filing_records": filing_record_count,
            "filing_documents": successful_documents,
            "filing_download_failures": len(filing_index) - successful_documents,
            "xbrl_concepts": xbrl_concept_count,
            "daily_bars": daily_bar_count,
            "corporate_actions": corporate_action_count,
            "macro_observations": sum(len(rows) for rows in macro_values.values()),
        },
        hashes={name: sha256_file(path) for name, path in sorted(asset_paths.items())},
        configuration={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "as_of": as_of.isoformat(),
            "feed": feed,
            "market_adjustment": "split",
            "forms": list(forms),
            "macro_series": normalized_series,
            "market_batch_size": market_batch_size,
            "maximum_filings_per_issuer": maximum_filings_per_issuer,
            "assets": {
                name: str(path.relative_to(run_directory))
                for name, path in sorted(asset_paths.items())
            },
        },
    )
    _write_json(manifest_path, manifest.model_dump(mode="json"))
    verify_authenticated_bundle(run_directory)
    return run_directory


async def _download_filing_record(
    sec: StreamingSecSource,
    member: UniverseMember,
    record: dict[str, Any],
    *,
    issuer_directory: Path,
    run_directory: Path,
    resume: bool,
) -> dict[str, Any]:
    accession = str(record["accessionNumber"])
    primary_document = str(record["primaryDocument"])
    document_path = issuer_directory / f"{accession}.html.gz"
    temporary_path = document_path.with_name(document_path.name + ".tmp")
    error: str | None = None
    available = resume and _has_verified_sidecar(document_path)
    if not available:
        try:
            html = await sec.filing_html(member.cik, accession, primary_document)
            with gzip.open(temporary_path, "wt", encoding="utf-8", compresslevel=6) as stream:
                stream.write(html)
            temporary_path.replace(document_path)
            _write_sidecar_hash(document_path)
            available = True
        except Exception as caught:  # network failures belong in the attrition audit
            temporary_path.unlink(missing_ok=True)
            error = f"{type(caught).__name__}: {caught}"[:500]
    if available:
        relative_path = str(document_path.relative_to(run_directory))
        document_hash = sha256_file(document_path)
        byte_count = document_path.stat().st_size
        status = "ok"
    else:
        relative_path = ""
        document_hash = ""
        byte_count = 0
        status = "failed"
    return {
        **record,
        "securityId": member.security_id or f"alpaca:{member.symbol}",
        "symbol": member.symbol,
        "companyName": member.company_name or record.get("companyName") or member.symbol,
        "exchange": member.exchange,
        "industryCode": member.industry_code or record.get("industryCode") or "unknown",
        "localPath": relative_path,
        "sha256": document_hash,
        "bytes": byte_count,
        "status": status,
        "error": error,
    }


def _assert_completed_request(
    root: Path, manifest: DatasetManifest, request: dict[str, Any]
) -> None:
    """Keep old completed checkpoints usable while refusing changed requests."""
    request_path = root / "request.json"
    if request_path.exists():
        if _read_json(request_path) != request:
            raise ValueError("Resume request differs from the finalized checkpoint")
        return
    configuration = manifest.configuration
    assets = configuration.get("assets")
    if not isinstance(assets, dict) or not isinstance(assets.get("requested_universe"), str):
        raise ValueError("Finalized checkpoint has no requested-universe identity")
    if _read_json(root / assets["requested_universe"]) != request["universe"]:
        raise ValueError("Resume universe differs from the finalized checkpoint")
    for field in (
        "start",
        "end",
        "as_of",
        "feed",
        "forms",
        "macro_series",
        "maximum_filings_per_issuer",
    ):
        if configuration.get(field) != request[field]:
            raise ValueError(f"Resume {field} differs from the finalized checkpoint")


def _sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def _has_verified_sidecar(path: Path) -> bool:
    sidecar = _sidecar_path(path)
    if not path.is_file() or not sidecar.is_file():
        return False
    try:
        expected = sidecar.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return False
    return (
        len(expected) == 64
        and all(character in "0123456789abcdef" for character in expected)
        and sha256_file(path) == expected
    )


def _write_sidecar_hash(path: Path) -> None:
    sidecar = _sidecar_path(path)
    temporary = sidecar.with_name(sidecar.name + ".tmp")
    temporary.write_text(sha256_file(path) + "\n", encoding="ascii")
    temporary.replace(sidecar)


def verify_authenticated_bundle(path: str | Path) -> DatasetManifest:
    """Validate checkpoint hashes, filing-document hashes, and path containment."""
    root = Path(path).resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    manifest = DatasetManifest.model_validate(_read_json(manifest_path))
    assets = manifest.configuration.get("assets")
    if not isinstance(assets, dict):
        raise ValueError("Manifest is missing its asset path map")
    for name, expected_hash in manifest.hashes.items():
        relative = assets.get(name)
        if not isinstance(relative, str):
            raise ValueError(f"Manifest path missing for asset: {name}")
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"Asset escapes checkpoint root: {name}")
        if not candidate.is_file():
            raise FileNotFoundError(f"Checkpoint asset not found: {candidate}")
        actual_hash = sha256_file(candidate)
        if actual_hash != expected_hash:
            raise ValueError(f"Hash mismatch for checkpoint asset: {name}")

    filing_index_relative = assets.get("filing_index")
    if isinstance(filing_index_relative, str):
        filing_rows = _read_json(root / filing_index_relative)
        for row in filing_rows:
            if row.get("status") != "ok":
                continue
            document_path = (root / str(row["localPath"])).resolve()
            if not document_path.is_relative_to(root):
                raise ValueError("Filing document escapes checkpoint root")
            if not document_path.is_file() or sha256_file(document_path) != row.get("sha256"):
                raise ValueError(
                    f"Filing document failed integrity check: {row.get('accessionNumber')}"
                )
    return manifest


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    serialized = orjson.dumps(
        payload,
        option=(orjson.OPT_INDENT_2 | orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_SORT_KEYS),
    )
    temporary.write_bytes(
        gzip.compress(serialized, compresslevel=6) if path.suffix == ".gz" else serialized
    )
    temporary.replace(path)
    return path


def _read_json(path: Path) -> Any:
    payload = path.read_bytes()
    return orjson.loads(gzip.decompress(payload) if path.suffix == ".gz" else payload)


def _count_nonempty_lines(path: Path) -> int:
    with path.open("rb") as stream:
        return sum(bool(line.strip()) for line in stream)


def _merge_list_payload(destination: dict[str, Any], payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        if isinstance(value, list):
            destination.setdefault(key, []).extend(value)
        elif isinstance(value, dict):
            nested = destination.setdefault(key, {})
            if not isinstance(nested, dict):
                raise ValueError(f"Payload changed type for {key}")
            _merge_list_payload(nested, value)
        elif key not in destination:
            destination[key] = value


def _count_nested_lists(payload: dict[str, Any]) -> int:
    total = 0
    for value in payload.values():
        if isinstance(value, list):
            total += len(value)
        elif isinstance(value, dict):
            total += _count_nested_lists(value)
    return total
