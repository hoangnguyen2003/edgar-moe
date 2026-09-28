"""Bounded SEC daily-index/cover-fact feasibility pilot; never a universe screen.

Live access is opt-in, paced at no more than two requests per second, and keeps
only an aggregate, identity-redacted report under ignored data/artifacts/.
This does not establish a complete historical listing master or authorize v2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

_ARTIFACT_ROOT = Path(__file__).resolve().parents[1] / "data" / "artifacts"
_HEADERS = {
    "CIK|Company Name|Form Type|Date Filed|File Name",
    "CIK|Company Name|Form Type|Date Filed|Filename",
}
_PATH = re.compile(r"edgar/data/(?P<cik>[0-9]+)/[0-9]{10}-[0-9]{2}-[0-9]{6}\.txt\Z")
_SYMBOL = re.compile(r"[A-Z0-9.^/-]{1,25}\Z")
_MAX_INDEX_BYTES = 8 * 1024 * 1024
_MAX_FILING_BYTES = 16 * 1024 * 1024
_MAX_SAMPLE = 12
_MIN_INDEX_LAG_DAYS = 7


class ArchivePilotError(RuntimeError):
    """A bounded feasibility or publication-safety precondition failed."""


@dataclass(frozen=True)
class IndexRecord:
    cik: str
    form: str
    filed_date: date
    path: str


@dataclass(frozen=True)
class CoverInspection:
    classification: str
    inline_xbrl_tag_present: bool
    xml_dei_symbol_tag_present: bool
    no_trading_symbol_flag_tag_present: bool


def parse_daily_master_index(payload: bytes, *, index_date: date) -> list[IndexRecord]:
    """Parse eligible periodic filings without retaining company names."""
    if not payload or len(payload) > _MAX_INDEX_BYTES:
        raise ArchivePilotError("SEC daily index is empty or exceeds the pilot bound")
    lines = payload.decode("latin-1").splitlines()
    header_at = next((index for index, line in enumerate(lines) if line in _HEADERS), None)
    if header_at is None:
        raise ArchivePilotError("SEC daily index header is missing")
    records: list[IndexRecord] = []
    seen_paths: set[str] = set()
    for line in lines[header_at + 1 :]:
        if not line.strip() or set(line.strip()) == {"-"}:
            continue
        fields = line.split("|")
        if len(fields) != 5:
            raise ArchivePilotError("SEC daily index row has an invalid field count")
        cik, company, form, filed, path = (field.strip() for field in fields)
        if form not in {"10-K", "10-Q"}:
            continue
        if not cik.isdecimal() or not company:
            raise ArchivePilotError("SEC daily index periodic row has invalid identity fields")
        match = _PATH.fullmatch(path)
        if match is None or int(match.group("cik")) != int(cik):
            raise ArchivePilotError("SEC daily index periodic path does not bind to CIK")
        try:
            filed_date = date.fromisoformat(filed)
        except ValueError as error:
            raise ArchivePilotError("SEC daily index periodic row has invalid date") from error
        if filed_date > index_date:
            raise ArchivePilotError("SEC daily index row is dated after its index")
        if path in seen_paths:
            raise ArchivePilotError("SEC daily index repeats a periodic filing path")
        seen_paths.add(path)
        records.append(IndexRecord(cik=cik.zfill(10), form=form, filed_date=filed_date, path=path))
    if not records:
        raise ArchivePilotError("SEC daily index has no eligible periodic filings")
    return records


def choose_sample(
    records: list[IndexRecord], *, index_date: date, cutoff: date, limit: int
) -> list[IndexRecord]:
    """Pick a reproducible CIK-distinct sample without looking at future data."""
    if not 1 <= limit <= _MAX_SAMPLE:
        raise ArchivePilotError("sample limit is outside the bounded pilot range")
    if index_date + timedelta(days=_MIN_INDEX_LAG_DAYS) > cutoff:
        raise ArchivePilotError("index date is too close to the declared cutoff")
    latest_by_cik: dict[str, IndexRecord] = {}
    for record in records:
        previous = latest_by_cik.get(record.cik)
        if previous is None or (record.filed_date, record.path) > (
            previous.filed_date,
            previous.path,
        ):
            latest_by_cik[record.cik] = record
    return sorted(
        latest_by_cik.values(),
        key=lambda record: hashlib.sha256(
            f"sec-daily-index-pilot-v1|{index_date}|{record.cik}|{record.path}".encode()
        ).digest(),
    )[:limit]


class _CoverFactParser(HTMLParser):
    """Count observed inline-XBRL cover facts without persisting their values."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.active: list[tuple[int, str, str, list[str]]] = []
        self.facts: list[tuple[str, str, str]] = []
        self.inline_xbrl_tag_present = False
        self.xml_dei_symbol_tag_present = False
        self.no_trading_symbol_flag_tag_present = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        if tag_name == "dei:tradingsymbol":
            self.xml_dei_symbol_tag_present = True
        if tag_name == "dei:notradingsymbolflag":
            self.no_trading_symbol_flag_tag_present = True
        if tag_name != "ix:nonnumeric":
            return
        self.inline_xbrl_tag_present = True
        self.depth += 1
        attributes = {key.lower(): value or "" for key, value in attrs}
        name = attributes.get("name", "").lower()
        if name == "dei:notradingsymbolflag":
            self.no_trading_symbol_flag_tag_present = True
        if name in {"dei:tradingsymbol", "dei:securityexchangename"}:
            self.active.append((self.depth, name, attributes.get("contextref", ""), []))

    def handle_data(self, data: str) -> None:
        for _depth, _name, _context, parts in self.active:
            parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "ix:nonnumeric":
            return
        if self.active and self.active[-1][0] == self.depth:
            _depth, name, context, parts = self.active.pop()
            value = re.sub(r"\s+", " ", "".join(parts)).strip()
            if value:
                self.facts.append((name, context, value))
        self.depth = max(self.depth - 1, 0)


def inspect_cover_facts(payload: bytes) -> CoverInspection:
    """Classify inline facts and non-identifying format indicators only."""
    if not payload or len(payload) > _MAX_FILING_BYTES:
        return CoverInspection("empty_or_oversize_filing", False, False, False)
    parser = _CoverFactParser()
    parser.feed(payload.decode("latin-1"))
    parser.close()
    symbols = {
        (context, value.upper())
        for name, context, value in parser.facts
        if name == "dei:tradingsymbol"
    }
    exchanges = {
        context for name, context, _value in parser.facts if name == "dei:securityexchangename"
    }
    if not symbols:
        classification = "missing_symbol_fact"
    elif len(symbols) != 1:
        classification = "ambiguous_symbol_or_class"
    else:
        context, symbol = next(iter(symbols))
        if not _SYMBOL.fullmatch(symbol):
            classification = "unsupported_symbol_format"
        elif not context or context not in exchanges:
            classification = "missing_matching_exchange_fact"
        else:
            classification = "single_symbol_exchange_context"
    return CoverInspection(
        classification,
        parser.inline_xbrl_tag_present,
        parser.xml_dei_symbol_tag_present,
        parser.no_trading_symbol_flag_tag_present,
    )


def classify_cover_facts(payload: bytes) -> str:
    """Classify inline cover-fact presence without returning symbol values."""
    return inspect_cover_facts(payload).classification


def assess_archive_sample(
    index_payload: bytes,
    *,
    index_date: date,
    cutoff: date,
    limit: int,
    fetch_filing: Callable[[str], bytes],
    live: bool,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    records = parse_daily_master_index(index_payload, index_date=index_date)
    sample = choose_sample(records, index_date=index_date, cutoff=cutoff, limit=limit)
    outcomes: Counter[str] = Counter()
    format_indicators = {
        "inline_xbrl_tag_present": 0,
        "xml_dei_symbol_tag_present": 0,
        "no_trading_symbol_flag_tag_present": 0,
    }
    bound_inputs: list[tuple[str, str]] = []
    for record in sample:
        filing = fetch_filing(record.path)
        if not isinstance(filing, bytes):
            raise ArchivePilotError("filing fetch did not return bytes")
        inspection = inspect_cover_facts(filing)
        outcomes[inspection.classification] += 1
        format_indicators["inline_xbrl_tag_present"] += int(inspection.inline_xbrl_tag_present)
        format_indicators["xml_dei_symbol_tag_present"] += int(
            inspection.xml_dei_symbol_tag_present
        )
        format_indicators["no_trading_symbol_flag_tag_present"] += int(
            inspection.no_trading_symbol_flag_tag_present
        )
        bound_inputs.append(
            (
                hashlib.sha256(record.path.encode()).hexdigest(),
                hashlib.sha256(filing).hexdigest(),
            )
        )
    manifest = json.dumps(sorted(bound_inputs), separators=(",", ":")).encode()
    return {
        "schema_version": 2,
        "status": "feasibility_only_not_historical_membership_evidence",
        "captured_at": (captured_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "source": "sec_daily_master_index_and_exact_filing_paths",
        "provider_contacted": live,
        "index_date": index_date.isoformat(),
        "declared_cutoff": cutoff.isoformat(),
        "conservative_index_lag_days": _MIN_INDEX_LAG_DAYS,
        "index_sha256": hashlib.sha256(index_payload).hexdigest(),
        "eligible_filing_rows_in_one_daily_index": len(records),
        "eligible_ciks_in_one_daily_index": len({record.cik for record in records}),
        "sample_method": "sha256_ranked_distinct_cik_latest_periodic_in_index",
        "sample_count": len(sample),
        "sample_input_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "cover_fact_classification_counts": dict(sorted(outcomes.items())),
        "format_indicator_counts": format_indicators,
        "limitations": [
            "one daily index is not a broad historical security master",
            "the seven-day lag is a pilot guard, not independent proof of historical archive bytes",
            "a cover-page symbol is not an independently evidenced listing interval",
            "a missing inline symbol does not prove the filing lacks a symbol in other formats",
            "format indicators may overlap and are not validated security mappings",
            "this sample does not establish historical market-bar rights or v2 eligibility",
        ],
    }


def _bounded_get(client: httpx.Client, url: str, *, maximum: int) -> bytes:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        result = bytearray()
        for chunk in response.iter_bytes():
            result.extend(chunk)
            if len(result) > maximum:
                raise ArchivePilotError("SEC response exceeds the pilot byte limit")
    return bytes(result)


def run_live(*, index_date: date, cutoff: date, limit: int, user_agent: str) -> dict[str, Any]:
    if "@" not in user_agent:
        raise ArchivePilotError("SEC_USER_AGENT with contact email is required")
    if not 1 <= limit <= _MAX_SAMPLE:
        raise ArchivePilotError("sample limit is outside the bounded pilot range")
    if index_date + timedelta(days=_MIN_INDEX_LAG_DAYS) > cutoff:
        raise ArchivePilotError("index date is too close to the declared cutoff")
    quarter = (index_date.month - 1) // 3 + 1
    index_url = (
        "https://www.sec.gov/Archives/edgar/daily-index/"
        f"{index_date.year}/QTR{quarter}/master.{index_date:%Y%m%d}.idx"
    )
    with httpx.Client(
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=45,
        follow_redirects=False,
    ) as client:
        index_payload = _bounded_get(client, index_url, maximum=_MAX_INDEX_BYTES)
        last_request_at = time.monotonic()

        def fetch_filing(path: str) -> bytes:
            nonlocal last_request_at
            elapsed = time.monotonic() - last_request_at
            if elapsed < 0.5:
                time.sleep(0.5 - elapsed)
            last_request_at = time.monotonic()
            return _bounded_get(
                client, f"https://www.sec.gov/Archives/{path}", maximum=_MAX_FILING_BYTES
            )

        return assess_archive_sample(
            index_payload,
            index_date=index_date,
            cutoff=cutoff,
            limit=limit,
            fetch_filing=fetch_filing,
            live=True,
        )


def write_report(report: dict[str, Any], path: Path, *, root: Path = _ARTIFACT_ROOT) -> None:
    destination = path.resolve()
    if not destination.is_relative_to(root.resolve()):
        raise ArchivePilotError("report output must be under ignored data/artifacts")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2, sort_keys=True)
        output.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="explicitly allow bounded SEC GETs")
    parser.add_argument("--index-date", required=True, type=date.fromisoformat)
    parser.add_argument("--cutoff", required=True, type=date.fromisoformat)
    parser.add_argument("--sample-limit", type=int, default=8)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if not args.live:
            raise ArchivePilotError("live SEC access requires --live")
        if not args.output.resolve().is_relative_to(_ARTIFACT_ROOT.resolve()):
            raise ArchivePilotError("report output must be under ignored data/artifacts")
        if args.output.exists():
            raise ArchivePilotError("report output already exists")
        if not 1 <= args.sample_limit <= _MAX_SAMPLE:
            raise ArchivePilotError("sample limit is outside the bounded pilot range")
        if args.index_date + timedelta(days=_MIN_INDEX_LAG_DAYS) > args.cutoff:
            raise ArchivePilotError("index date is too close to the declared cutoff")
        report = run_live(
            index_date=args.index_date,
            cutoff=args.cutoff,
            limit=args.sample_limit,
            user_agent=os.environ.get("SEC_USER_AGENT", ""),
        )
        write_report(report, args.output)
    except ArchivePilotError as error:
        # Every ArchivePilotError in this module has a static, identity-free message.
        print(f"SEC archive pilot failed: {error}", file=sys.stderr)
        return 1
    except (httpx.HTTPError, OSError, ValueError) as error:
        # HTTP/OS details may include a URL or local configuration. Never log them.
        print(f"SEC archive pilot failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(f"Identity-redacted SEC archive pilot written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
