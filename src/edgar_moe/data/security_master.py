from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher
from typing import Any

from edgar_moe.data.contracts import MappingStatus, SecurityMapping

CORPORATE_SUFFIXES = re.compile(
    r"\b(incorporated|inc|corp(?:oration)?|company|co|ltd|limited|plc|holdings?)\b", re.I
)


def normalize_company_name(value: str) -> str:
    value = CORPORATE_SUFFIXES.sub("", value)
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def build_security_mapping(
    sec_company: dict[str, Any],
    alpaca_assets: Iterable[dict[str, Any]],
    threshold: float = 0.85,
) -> SecurityMapping | None:
    """Map a SEC filer to an Alpaca asset with auditable confidence evidence."""
    cik = str(sec_company.get("cik_str") or sec_company.get("cik") or "").zfill(10)
    sec_tickers = {str(item).upper() for item in sec_company.get("tickers", [])}
    sec_name = str(sec_company.get("title") or sec_company.get("name") or "")
    normalized_sec_name = normalize_company_name(sec_name)

    best: tuple[float, dict[str, Any], list[str]] | None = None
    for asset in alpaca_assets:
        symbol = str(asset.get("symbol", "")).upper()
        asset_name = str(asset.get("name", ""))
        evidence: list[str] = []
        score = 0.0
        if symbol in sec_tickers:
            score += 0.72
            evidence.append("exact_ticker")
        similarity = SequenceMatcher(
            None, normalized_sec_name, normalize_company_name(asset_name)
        ).ratio()
        score += 0.28 * similarity
        if similarity >= 0.9:
            evidence.append("strong_name_match")
        if best is None or score > best[0]:
            best = (score, asset, evidence)

    if best is None:
        return None
    confidence, asset, evidence = best
    status = (
        MappingStatus.CONFIDENT
        if confidence >= threshold
        else MappingStatus.REVIEW
        if confidence >= 0.7
        else MappingStatus.EXCLUDED
    )
    return SecurityMapping(
        security_id=str(asset.get("id") or f"alpaca:{asset.get('symbol')}"),
        cik=cik,
        symbol=str(asset.get("symbol", "")).upper(),
        company_name=sec_name,
        exchange=str(asset.get("exchange", "")),
        active=asset.get("status") == "active",
        confidence=round(confidence, 6),
        status=status,
        evidence=evidence,
    )
