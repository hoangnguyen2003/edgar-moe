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


def normalize_symbol(value: str) -> str:
    """Normalize share-class punctuation for cross-source ticker matching."""
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def build_security_mapping(
    sec_company: dict[str, Any],
    alpaca_assets: Iterable[dict[str, Any]],
    threshold: float = 0.85,
) -> SecurityMapping | None:
    """Map a SEC filer to an Alpaca asset with auditable confidence evidence."""
    cik = str(sec_company.get("cik_str") or sec_company.get("cik") or "").zfill(10)
    sec_tickers = {str(item).upper() for item in sec_company.get("tickers", [])}
    normalized_sec_tickers = {normalize_symbol(item) for item in sec_tickers}
    sec_name = str(sec_company.get("title") or sec_company.get("name") or "")
    normalized_sec_name = normalize_company_name(sec_name)

    best: tuple[float, dict[str, Any], list[str]] | None = None
    for asset in alpaca_assets:
        symbol = str(asset.get("symbol", "")).upper()
        asset_name = str(asset.get("name", ""))
        evidence: list[str] = []
        score = 0.0
        if normalize_symbol(symbol) in normalized_sec_tickers:
            score += 0.72
            evidence.append("exact_ticker" if symbol in sec_tickers else "normalized_ticker")
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


def build_security_master(
    sec_exchange_payload: dict[str, Any],
    alpaca_assets: Iterable[dict[str, Any]],
    threshold: float = 0.85,
) -> list[SecurityMapping]:
    """Build an auditable SEC-to-market security master from official source snapshots."""
    fields = sec_exchange_payload.get("fields", [])
    data = sec_exchange_payload.get("data", [])
    if not isinstance(fields, list) or not isinstance(data, list):
        raise ValueError("Unexpected SEC company_tickers_exchange payload")
    grouped: dict[str, dict[str, Any]] = {}
    for values in data:
        if not isinstance(values, list) or len(values) != len(fields):
            continue
        row = dict(zip(fields, values, strict=True))
        cik = str(row.get("cik") or row.get("cik_str") or "").zfill(10)
        ticker = str(row.get("ticker") or "").upper()
        if not cik.strip("0") or not ticker:
            continue
        company = grouped.setdefault(
            cik,
            {
                "cik_str": cik,
                "title": str(row.get("name") or ""),
                "tickers": [],
                "exchanges": [],
            },
        )
        company["tickers"].append(ticker)
        company["exchanges"].append(str(row.get("exchange") or ""))

    assets = list(alpaca_assets)
    assets_by_symbol: dict[str, list[dict[str, Any]]] = {}
    assets_by_name: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        normalized_symbol = normalize_symbol(str(asset.get("symbol") or ""))
        normalized_name = normalize_company_name(str(asset.get("name") or ""))
        if normalized_symbol:
            assets_by_symbol.setdefault(normalized_symbol, []).append(asset)
        if normalized_name:
            assets_by_name.setdefault(normalized_name, []).append(asset)

    mappings: list[SecurityMapping] = []
    for company in grouped.values():
        candidates_by_id: dict[str, dict[str, Any]] = {}
        for ticker in company["tickers"]:
            for asset in assets_by_symbol.get(normalize_symbol(str(ticker)), []):
                key = str(asset.get("id") or f"alpaca:{asset.get('symbol')}")
                candidates_by_id[key] = asset
        for asset in assets_by_name.get(normalize_company_name(str(company["title"])), []):
            key = str(asset.get("id") or f"alpaca:{asset.get('symbol')}")
            candidates_by_id[key] = asset

        mapping = build_security_mapping(company, candidates_by_id.values(), threshold)
        if mapping is not None:
            mappings.append(mapping)
            continue
        mappings.append(
            SecurityMapping(
                security_id=f"unmapped:{company['cik_str']}",
                cik=str(company["cik_str"]),
                symbol=str(company["tickers"][0]),
                company_name=str(company["title"]),
                exchange=str(company["exchanges"][0]),
                active=False,
                confidence=0.0,
                status=MappingStatus.EXCLUDED,
                evidence=["no_alpaca_candidate"],
            )
        )
    return sorted(mappings, key=lambda item: (item.status.value, item.symbol, item.cik))
