"""Dated SEC evidence for a fixed cohort's issuer/share-class/CUSIP join.

This module is offline. A source row, current ticker or current asset UUID is
never sufficient. Regulatory publication timestamps and exact document bytes
must accompany both legs of the join. It does not authenticate a caller's
source files or authorize research/publication.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup

MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
PUBLICATION_LAG_DAYS = 7
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,14}")
_NAMES = {
    "dei:entitycentralindexkey",
    "dei:tradingsymbol",
    "dei:securityexchangename",
    "dei:security12btitle",
}


class IdentityEvidenceError(ValueError):
    """Static refusal code, suitable for an operational report without rows."""


def instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise IdentityEvidenceError("invalid_source_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise IdentityEvidenceError("invalid_source_timestamp") from error
    if parsed.utcoffset() is None:
        raise IdentityEvidenceError("naive_source_timestamp")
    return parsed.astimezone(UTC)


def source_metadata(
    metadata: dict[str, Any], *, cik: str, cutoff: datetime, forms: set[str]
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9]{10}", cik) is None or int(cik) == 0:
        raise IdentityEvidenceError("invalid_issuer_identity")
    if (
        metadata.get("cik") != cik
        or metadata.get("form") not in forms
        or not isinstance(metadata.get("accessionNumber"), str)
        or re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", metadata["accessionNumber"]) is None
    ):
        raise IdentityEvidenceError("filing_metadata_identity_mismatch")
    accepted = instant(metadata.get("acceptanceDateTime"))
    available = accepted + timedelta(days=PUBLICATION_LAG_DAYS)
    if cutoff.utcoffset() is None or available > cutoff:
        raise IdentityEvidenceError("filing_not_available_by_cohort_cutoff")
    return {
        "issuer_cik": cik,
        "form": metadata["form"],
        "accession": metadata["accessionNumber"],
        "accepted_at": accepted.isoformat(),
        "available_by": available.isoformat(),
        "availability_basis": "SEC_reported_acceptance_plus_seven_calendar_days",
    }


def _soup(payload: bytes) -> BeautifulSoup:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("empty_or_excessive_document")
    return BeautifulSoup(payload, "html.parser")


def class_signature(value: str) -> tuple[str, str, str]:
    """Conservative class equivalence, ignoring par-value amounts, not class rights."""
    text = " ".join(value.upper().split())
    if re.search(
        r"\b(PREFERRED|PREFERENCE|DEPOSITARY|DEPOSITORY|WARRANTS?|NOTES?|BONDS?|UNITS?)\b", text
    ):
        raise IdentityEvidenceError("unsupported_security_class")
    family = "common" if re.search(r"\bCOMMON\s+(?:STOCK|SHARES)\b", text) else ""
    if re.search(r"\bORDINARY\s+(?:STOCK|SHARES)\b", text):
        if family:
            raise IdentityEvidenceError("ambiguous_security_class")
        family = "ordinary"
    classes = set(re.findall(r"\bCLASS\s+([A-Z0-9]+)\b", text))
    if not family or len(classes) > 1:
        raise IdentityEvidenceError("ambiguous_security_class")
    voting = "nonvoting" if re.search(r"\bNON[ -]?VOTING\b", text) else ""
    if not voting and re.search(r"\bVOTING\b", text):
        voting = "voting"
    return family, next(iter(classes), ""), voting


def periodic_anchor(
    payload: bytes, metadata: dict[str, Any], *, cik: str, symbol: str, cutoff: datetime
) -> dict[str, Any]:
    source = source_metadata(metadata, cik=cik, cutoff=cutoff, forms={"10-K", "10-Q"})
    if _SYMBOL.fullmatch(symbol) is None:
        raise IdentityEvidenceError("invalid_snapshot_symbol")
    soup = _soup(payload)
    grouped: dict[str, dict[str, set[str]]] = {}
    issuers: set[str] = set()
    count = 0
    for tag in soup.find_all("ix:nonnumeric"):
        name = str(tag.get("name", "")).lower()
        if name not in _NAMES:
            continue
        count += 1
        if count > 1024:
            raise IdentityEvidenceError("excessive_cover_facts")
        value = " ".join(tag.get_text(" ", strip=True).split())
        context = str(tag.get("contextref", ""))
        if name == "dei:entitycentralindexkey":
            if not re.fullmatch(r"[0-9]{1,10}", value):
                raise IdentityEvidenceError("invalid_document_issuer")
            issuers.add(value.zfill(10))
        if context and value:
            grouped.setdefault(context, {}).setdefault(name, set()).add(value)
    context_issuers: dict[str, set[str]] = {}
    for context_tag in soup.find_all("xbrli:context"):
        context_id = str(context_tag.get("id", ""))
        identifiers = context_tag.find_all("xbrli:identifier")
        for identifier in identifiers:
            if identifier.get("scheme") not in {
                "http://www.sec.gov/CIK",
                "https://www.sec.gov/CIK",
            }:
                continue
            value = identifier.get_text(strip=True)
            if not re.fullmatch(r"[0-9]{1,10}", value):
                raise IdentityEvidenceError("invalid_context_issuer")
            context_issuers.setdefault(context_id, set()).add(value.zfill(10))
    if not context_issuers and issuers != {cik}:
        raise IdentityEvidenceError("document_issuer_mismatch")
    matches: set[tuple[str, str]] = set()
    for context_id, fields in grouped.items():
        if fields.get("dei:tradingsymbol") != {symbol}:
            continue
        # Combined reports may contain DEI facts for several registrants.
        # Bind the requested share-class facts to their own issuer context.
        if context_issuers and context_issuers.get(context_id) != {cik}:
            raise IdentityEvidenceError("symbol_context_issuer_mismatch")
        titles = fields.get("dei:security12btitle", set())
        exchanges = fields.get("dei:securityexchangename", set())
        if len(titles) != 1 or len(exchanges) != 1:
            raise IdentityEvidenceError("ambiguous_registered_class")
        title, exchange = next(iter(titles)), next(iter(exchanges))
        class_signature(title)
        matches.add((title, exchange))
    if len(matches) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_symbol_class_context")
    title, exchange = next(iter(matches))
    return {
        **source,
        "symbol_at_cutoff": symbol,
        "security_title": title,
        "exchange_at_filing": exchange,
        "document_sha256": hashlib.sha256(payload).hexdigest(),
    }


def valid_cusip(value: str) -> bool:
    if re.fullmatch(r"[A-Z0-9]{8}[0-9]", value) is None:
        return False
    total = 0
    for index, char in enumerate(value[:8]):
        number = int(char) if char.isdigit() else ord(char) - 55
        number *= 1 if index % 2 == 0 else 2
        total += number // 10 + number % 10
    return (10 - total % 10) % 10 == int(value[8])


def ownership_identity(
    payload: bytes, metadata: dict[str, Any], *, cik: str, cutoff: datetime
) -> dict[str, Any]:
    source = source_metadata(
        metadata, cik=cik, cutoff=cutoff, forms={"SC 13G", "SC 13G/A", "SC 13D", "SC 13D/A"}
    )
    text = " ".join(_soup(payload).get_text(" ", strip=True).split())
    titles = re.findall(
        r"\(\s*Name\s+of\s+Issuer\s*\)(.*?)\(\s*Title\s+of\s+Class\s+of\s+Securities\s*\)",
        text,
        re.I,
    )
    if not titles:
        raise IdentityEvidenceError("missing_ownership_class")
    signatures = {class_signature(title) for title in titles}
    if len(signatures) != 1:
        raise IdentityEvidenceError("ambiguous_ownership_class")
    candidates: set[str] = set()
    for label in re.finditer(r"CUSIP\s+(?:Number|No\.?)", text, re.I):
        region = text[max(0, label.start() - 64) : label.end() + 64]
        for match in re.finditer(r"(?<![A-Z0-9])([A-Z0-9]{8}[0-9])(?![A-Z0-9])", region):
            if valid_cusip(match[1]):
                candidates.add(match[1])
    # Some covers separate a CUSIP into groups with whitespace.
    for field in re.findall(
        r"\(\s*Title\s+of\s+Class\s+of\s+Securities\s*\)(.*?)\(\s*CUSIP\s+(?:Number|No\.?)[^)]*\)",
        text,
        re.I,
    ):
        value = re.sub(r"[\s-]", "", field).upper()
        if valid_cusip(value):
            candidates.add(value)
    if len(candidates) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_cusip")
    return {
        **source,
        "cusip": next(iter(candidates)),
        "security_title": titles[0].strip(),
        "document_sha256": hashlib.sha256(payload).hexdigest(),
    }


def join_identity(periodic: dict[str, Any], ownership: dict[str, Any]) -> dict[str, Any]:
    if periodic["issuer_cik"] != ownership["issuer_cik"]:
        raise IdentityEvidenceError("joined_issuer_mismatch")
    if class_signature(periodic["security_title"]) != class_signature(ownership["security_title"]):
        raise IdentityEvidenceError("joined_share_class_mismatch")
    if not valid_cusip(ownership["cusip"]):
        raise IdentityEvidenceError("invalid_joined_cusip")
    return {
        "issuer_cik": periodic["issuer_cik"],
        "security_id": "cusip:" + ownership["cusip"],
        "symbol_at_cutoff": periodic["symbol_at_cutoff"],
        "security_title": periodic["security_title"],
        "exchange_at_filing": periodic["exchange_at_filing"],
        "available_by": max(
            instant(periodic["available_by"]), instant(ownership["available_by"])
        ).isoformat(),
        "periodic_evidence": periodic,
        "ownership_evidence": ownership,
        "identity_basis": "same_SEC_issuer_and_share_class_with_pre_cutoff_reported_CUSIP",
        "current_asset_directory_used_for_selection": False,
    }
