from __future__ import annotations

import asyncio
import gzip
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

SEC_DATA_BASE = "https://data.sec.gov"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives"


class SecClient:
    """Small, cache-aware EDGAR client with conservative request pacing."""

    def __init__(
        self,
        user_agent: str,
        cache_dir: str | Path,
        requests_per_second: int = 8,
        transport: httpx.AsyncBaseTransport | None = None,
        cache_filings: bool = True,
    ) -> None:
        if "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT should include a contact email")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.minimum_interval = 1.0 / min(max(requests_per_second, 1), 8)
        self.cache_filings = cache_filings
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json,text/html",
            },
            timeout=45,
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self) -> SecClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    async def _pace(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait_for = self.minimum_interval - (now - self._last_request)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_request = loop.time()

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
    async def _get(self, url: str) -> httpx.Response:
        await self._pace()
        response = await self.client.get(url)
        response.raise_for_status()
        return response

    async def submissions(self, cik: str) -> dict[str, Any]:
        cik = cik.zfill(10)
        response = await self._get(f"{SEC_DATA_BASE}/submissions/CIK{cik}.json")
        return cast(dict[str, Any], response.json())

    async def submission_file(self, name: str) -> dict[str, Any]:
        """Load an older columnar submissions file referenced by a filer payload."""
        if not re.fullmatch(r"CIK\d{10}-submissions-\d{3}\.json", name):
            raise ValueError(f"Unexpected SEC submissions filename: {name}")
        response = await self._get(f"{SEC_DATA_BASE}/submissions/{name}")
        return cast(dict[str, Any], response.json())

    async def complete_submissions(self, cik: str, start: date | None = None) -> dict[str, Any]:
        """Load current and referenced historical filing rows for one filer."""
        payload = await self.submissions(cik)
        descriptors = payload.get("filings", {}).get("files", [])
        histories: list[dict[str, Any]] = []
        for descriptor in descriptors:
            if not isinstance(descriptor, dict) or not descriptor.get("name"):
                continue
            if start is not None and descriptor.get("filingTo"):
                try:
                    if date.fromisoformat(str(descriptor["filingTo"])) < start:
                        continue
                except ValueError:
                    pass
            histories.append(await self.submission_file(str(descriptor["name"])))
        payload.setdefault("filings", {})["recent"] = merge_submission_columns(
            payload.get("filings", {}).get("recent", {}), *histories
        )
        return payload

    async def company_facts(self, cik: str) -> dict[str, Any]:
        cik = cik.zfill(10)
        response = await self._get(f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK{cik}.json")
        return cast(dict[str, Any], response.json())

    async def company_tickers_exchange(self) -> dict[str, Any]:
        response = await self._get("https://www.sec.gov/files/company_tickers_exchange.json")
        return cast(dict[str, Any], response.json())

    async def filing_html(self, cik: str, accession: str, primary_document: str) -> str:
        compact = accession.replace("-", "")
        cache_path = self.cache_dir / f"{accession}-{Path(primary_document).name}.gz"
        if self.cache_filings and cache_path.exists():
            with gzip.open(cache_path, "rt", encoding="utf-8", errors="replace") as stream:
                return stream.read()
        url = f"{SEC_ARCHIVES_BASE}/edgar/data/{int(cik)}/{compact}/{primary_document}"
        response = await self._get(url)
        if self.cache_filings:
            with gzip.open(cache_path, "wt", encoding="utf-8", compresslevel=6) as stream:
                stream.write(response.text)
        return response.text


@dataclass(frozen=True)
class FilingSection:
    name: str
    text: str


class _VisibleTextParser(HTMLParser):
    """Stream visible filing text without constructing a multi-megabyte DOM."""

    IGNORED_ELEMENTS = {"script", "style", "table", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in self.IGNORED_ELEMENTS:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.IGNORED_ELEMENTS and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


class FilingParser:
    """Deterministic filing text cleaner and section extractor."""

    ITEM_PATTERNS = {
        "risk_factors": (
            re.compile(r"\bitem\s+1a[.\s:-]+risk\s+factors\b", re.I),
            re.compile(r"\bitem\s+1b\b|\bitem\s+2\b", re.I),
        ),
        "management_discussion": (
            re.compile(r"\bitem\s+(?:7|2)[.\s:-]+management(?:'s|’s)?\s+discussion", re.I),
            re.compile(r"\bitem\s+(?:7a|8|3)\b", re.I),
        ),
    }

    @staticmethod
    def clean_html(html: str) -> str:
        parser = _VisibleTextParser()
        parser.feed(html)
        parser.close()
        text = " ".join(parser.parts)
        return re.sub(r"\s+", " ", text).strip()

    def extract_sections(self, html: str) -> dict[str, FilingSection]:
        text = self.clean_html(html)
        lowered = text.lower()
        sections: dict[str, FilingSection] = {}
        for name, (start_pattern, end_pattern) in self.ITEM_PATTERNS.items():
            starts = list(start_pattern.finditer(lowered))
            if not starts:
                continue
            # Tables of contents usually contain the first occurrence; the longest
            # plausible candidate is more robust than taking it positionally.
            candidates: list[str] = []
            for match in starts[:8]:
                end = end_pattern.search(lowered, match.end())
                end_index = end.start() if end else min(len(text), match.end() + 120_000)
                candidate = text[match.start() : end_index].strip()
                if 300 <= len(candidate) <= 150_000:
                    candidates.append(candidate)
            if candidates:
                sections[name] = FilingSection(name=name, text=max(candidates, key=len))
        return sections


def parse_sec_acceptance(value: str) -> datetime:
    """Parse EDGAR acceptance strings into UTC."""
    normalized = value.strip().replace(" ", "T")
    if re.fullmatch(r"\d{14}", normalized):
        parsed = datetime.strptime(normalized, "%Y%m%d%H%M%S")
    else:
        parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        # SEC acceptance times are Eastern; callers should prefer ISO offsets.
        from zoneinfo import ZoneInfo

        parsed = parsed.replace(tzinfo=ZoneInfo("America/New_York"))
    return parsed.astimezone(UTC)


def merge_submission_columns(*payloads: dict[str, Any]) -> dict[str, list[Any]]:
    """Merge SEC columnar filing payloads while retaining every observed field."""
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        rows.extend(submission_rows(payload))
    columns = sorted({key for row in rows for key in row})
    return {column: [row.get(column) for row in rows] for column in columns}


def submission_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a SEC compact columnar filing object into ordinary row dictionaries."""
    if not payload:
        return []
    column_values = {str(key): value for key, value in payload.items() if isinstance(value, list)}
    if not column_values:
        return []
    row_count = max((len(values) for values in column_values.values()), default=0)
    return [
        {
            key: values[index] if index < len(values) else None
            for key, values in column_values.items()
        }
        for index in range(row_count)
    ]


def periodic_filing_records(
    submissions: dict[str, Any],
    *,
    start: date,
    end: date,
    forms: tuple[str, ...] = ("10-K", "10-Q"),
    maximum: int | None = None,
) -> list[dict[str, Any]]:
    """Return valid, non-amended periodic filings in acceptance-time order."""
    if maximum is not None and maximum < 1:
        raise ValueError("maximum must be positive when provided")
    cik = str(submissions.get("cik") or "").zfill(10)
    company_name = str(submissions.get("name") or "")
    industry_code = str(submissions.get("sic") or "unknown")
    allowed_forms = set(forms)
    selected: dict[str, dict[str, Any]] = {}
    recent = submissions.get("filings", {}).get("recent", {})
    for row in submission_rows(recent if isinstance(recent, dict) else {}):
        form = str(row.get("form") or "")
        accession = str(row.get("accessionNumber") or "")
        primary_document = str(row.get("primaryDocument") or "")
        acceptance_value = str(row.get("acceptanceDateTime") or "")
        report_value = str(row.get("reportDate") or "")
        if form not in allowed_forms or not accession or not primary_document:
            continue
        try:
            accepted_at = parse_sec_acceptance(acceptance_value)
            report_period = date.fromisoformat(report_value)
        except (TypeError, ValueError):
            continue
        if not (
            datetime.combine(start, time.min, tzinfo=UTC)
            <= accepted_at
            <= datetime.combine(end, time.max, tzinfo=UTC)
        ):
            continue
        compact_accession = accession.replace("-", "")
        filing_url = (
            f"{SEC_ARCHIVES_BASE}/edgar/data/{int(cik)}/{compact_accession}/"
            f"{Path(primary_document).name}"
        )
        selected[accession] = {
            **row,
            "accessionNumber": accession,
            "acceptanceDateTime": accepted_at.isoformat(),
            "reportDate": report_period.isoformat(),
            "form": form,
            "primaryDocument": Path(primary_document).name,
            "cik": cik,
            "companyName": company_name,
            "industryCode": industry_code,
            "filingUrl": filing_url,
        }
    records = sorted(selected.values(), key=lambda item: item["acceptanceDateTime"])
    return records[-maximum:] if maximum is not None else records
