from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
from bs4 import BeautifulSoup
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
    ) -> None:
        if "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT should include a contact email")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.minimum_interval = 1.0 / min(max(requests_per_second, 1), 8)
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

    async def company_facts(self, cik: str) -> dict[str, Any]:
        cik = cik.zfill(10)
        response = await self._get(f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK{cik}.json")
        return cast(dict[str, Any], response.json())

    async def filing_html(self, cik: str, accession: str, primary_document: str) -> str:
        compact = accession.replace("-", "")
        cache_path = self.cache_dir / f"{accession}-{Path(primary_document).name}"
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8", errors="replace")
        url = f"{SEC_ARCHIVES_BASE}/edgar/data/{int(cik)}/{compact}/{primary_document}"
        response = await self._get(url)
        cache_path.write_text(response.text, encoding="utf-8")
        return response.text


@dataclass(frozen=True)
class FilingSection:
    name: str
    text: str


class FilingParser:
    """Deterministic filing text cleaner and section extractor."""

    ITEM_PATTERNS = {
        "risk_factors": (
            re.compile(r"\bitem\s+1a[.\s:-]+risk\s+factors\b", re.I),
            re.compile(r"\bitem\s+1b\b|\bitem\s+2\b", re.I),
        ),
        "management_discussion": (
            re.compile(
                r"\bitem\s+(?:7|2)[.\s:-]+management(?:'s|’s)?\s+discussion", re.I
            ),
            re.compile(r"\bitem\s+(?:7a|8|3)\b", re.I),
        ),
    }

    @staticmethod
    def clean_html(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "table", "noscript"]):
            element.decompose()
        text = soup.get_text(" ")
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
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        # SEC acceptance times are Eastern; callers should prefer ISO offsets.
        from zoneinfo import ZoneInfo

        parsed = parsed.replace(tzinfo=ZoneInfo("America/New_York"))
    return parsed.astimezone(UTC)
