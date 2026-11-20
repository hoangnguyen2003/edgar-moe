"""Bind a bank's FDIC registration row to the same equity in an SEC prospectus."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import Tag

from .historical_identity import (
    MAX_DOCUMENT_BYTES,
    PUBLICATION_LAG_DAYS,
    IdentityEvidenceError,
    _soup,
    class_signature,
    instant,
    valid_cusip,
)


def _one(matches: list[Any], failure: str) -> Any:
    if len(matches) != 1:
        raise IdentityEvidenceError(failure)
    return matches[0]


def _text(tag: Tag) -> str:
    return " ".join(tag.get_text(" ", strip=True).split())


def filed_equity_reference(
    raw: bytes,
    reference: dict[str, str],
    periodic: dict[str, Any],
    *,
    cutoff: datetime,
) -> dict[str, Any]:
    """Read only an explicitly named underlying equity section and its identifier."""
    if not 0 < len(raw) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("invalid_equity_prospectus_size")
    if (
        cutoff.utcoffset() is None
        or instant(periodic["available_by"]) > cutoff
        or not periodic["accession"].startswith("FDIC:")
        or class_signature(periodic["security_title"]) != ("common", "", "")
    ):
        raise IdentityEvidenceError("invalid_equity_prospectus_periodic_anchor")
    header = _one(
        re.findall(rb"<SEC-HEADER>(.*?)</SEC-HEADER>", raw, re.S),
        "ambiguous_equity_prospectus_header",
    ).replace(b"\r\n", b"\n")
    if len(header) > 256 * 1024:
        raise IdentityEvidenceError("equity_prospectus_header_size")
    for label, expected in (
        (b"ACCESSION NUMBER", reference["accession"].encode()),
        (b"CONFORMED SUBMISSION TYPE", b"424B2"),
    ):
        if re.findall(rb"(?m)^" + label + rb":[ \t]*([^\n]+)", header) != [expected]:
            raise IdentityEvidenceError("equity_prospectus_header_identity_mismatch")
    filers = re.findall(rb"(?ms)^FILER:[ \t]*\n(.*?)(?=^\S|\Z)", header)
    filer_ids = []
    for filer in filers:
        filer_ids.append(
            _one(
                re.findall(rb"(?m)^[ \t]*CENTRAL INDEX KEY:[ \t]*([0-9]{10})[ \t]*$", filer),
                "ambiguous_equity_prospectus_filer",
            ).decode("ascii")
        )
    if (
        not 1 <= len(filer_ids) <= 4
        or len(set(filer_ids)) != len(filer_ids)
        or reference["filer_cik"] not in filer_ids
        or periodic["issuer_cik"] in filer_ids
    ):
        raise IdentityEvidenceError("invalid_equity_prospectus_filers")
    accepted_at = _one(
        re.findall(rb"<ACCEPTANCE-DATETIME>([0-9]{14})[ \t]*\n", header),
        "missing_equity_prospectus_acceptance",
    )
    filed = _one(
        re.findall(rb"(?m)^FILED AS OF DATE:[ \t]*([0-9]{8})[ \t]*$", header),
        "missing_equity_prospectus_filing_date",
    )
    accepted_local = datetime.strptime(accepted_at.decode(), "%Y%m%d%H%M%S").replace(
        tzinfo=ZoneInfo("America/New_York")
    )
    accepted = accepted_local.astimezone(UTC)
    available = accepted + timedelta(days=PUBLICATION_LAG_DAYS)
    if (
        accepted_local.strftime("%Y%m%d").encode() != filed
        or available > cutoff
        or cutoff - accepted > timedelta(days=730)
    ):
        raise IdentityEvidenceError("equity_prospectus_unavailable_at_cutoff")

    documents = re.findall(rb"<DOCUMENT>(.*?)</DOCUMENT>", raw, re.S)
    primary = _one(
        [doc for doc in documents if re.search(rb"(?m)^<TYPE>424B2[ \t\r]*$", doc)],
        "missing_or_ambiguous_equity_prospectus_document",
    )
    if re.findall(rb"(?m)^<FILENAME>([^\r\n]+)", primary) != [reference["filename"].encode()]:
        raise IdentityEvidenceError("equity_prospectus_filename_mismatch")
    html = _one(
        re.findall(rb"<TEXT>(.*?)</TEXT>", primary, re.S),
        "missing_or_ambiguous_equity_prospectus_text",
    )
    soup = _soup(html)
    name = periodic["reported_issuer_name"]
    heading = _one(
        [p for p in soup.find_all("p") if _text(p) == name + " Overview"],
        "ambiguous_equity_prospectus_overview",
    )
    section = heading.parent
    if section is None or section.name != "div":
        raise IdentityEvidenceError("unbounded_equity_prospectus_overview")
    tags = section.find_all(recursive=False)
    if not tags or tags[0] is not heading:
        raise IdentityEvidenceError("unbounded_equity_prospectus_overview")
    paragraphs = [_text(tag) for tag in tags if tag.name == "p"]
    cited = [
        (m[1], m[2])
        for p in paragraphs
        for m in re.finditer(
            r"by (.+?) pursuant to the Exchange Act can be located by reference to the "
            r"FDIC certificate number ([1-9][0-9]{0,5})\b",
            p,
        )
    ]
    if cited != [(name, str(periodic["certificate"]))]:
        raise IdentityEvidenceError("equity_prospectus_bank_certificate_conflict")
    tables = [tag for tag in tags if tag.name == "table"]
    market, prices = tables if len(tables) == 2 else (None, None)
    if market is None or prices is None:
        raise IdentityEvidenceError("equity_prospectus_table_structure")
    rows = [
        [_text(cell) for cell in row.find_all("td", recursive=False)]
        for row in market.find_all("tr")
    ]
    ticker = [r[1:] for r in rows if r and r[0] == "Bloomberg Ticker Symbol:"]
    exchange = [r[1:] for r in rows if r and r[0] == "Exchange:"]
    mic_name = {"NASDAQ Global Select Market": "Nasdaq", "New York Stock Exchange": "NYSE"}.get(
        periodic["exchange_at_filing"]
    )
    if ticker != [[periodic["symbol_at_cutoff"]]] or mic_name is None or exchange != [[mic_name]]:
        raise IdentityEvidenceError("equity_prospectus_exchange_or_ticker_conflict")
    as_of_text = _one(
        [
            m[1]
            for p in paragraphs
            if (
                m := re.fullmatch(
                    r"Information as of market close on ([A-Za-z]+ [0-9]{1,2}, [0-9]{4}):", p
                )
            )
        ],
        "missing_equity_prospectus_observation_date",
    )
    as_of = datetime.strptime(as_of_text, "%B %d, %Y").date()
    if not 0 <= (accepted_local.date() - as_of).days <= 30:
        raise IdentityEvidenceError("equity_prospectus_observation_date_conflict")
    cells = next(
        (
            [_text(cell) for cell in row.find_all("td", recursive=False)]
            for row in prices.find_all("tr")
            if _text(row)
        ),
        [],
    )
    description = re.fullmatch(
        rf"Common Stock of {re.escape(name)} \(CUSIP ([0-9A-Z]{{8}}[0-9])\)",
        cells[0] if cells[1:] == ["High ($)", "Low ($)", "Dividends ($)"] else "",
    )
    if description is None or not valid_cusip(description[1]):
        raise IdentityEvidenceError("equity_prospectus_common_class_identifier_missing")
    if sum("(CUSIP " in _text(cell) for cell in prices.find_all("td")) != 1:
        raise IdentityEvidenceError("equity_prospectus_multiple_identifiers")

    return {
        "document_sha256": hashlib.sha256(raw).hexdigest(),
        "accession": reference["accession"],
        "filer_cik": reference["filer_cik"],
        "form": "424B2",
        "accepted_at": accepted.isoformat(),
        "available_by": available.isoformat(),
        "source_observation_date": as_of.isoformat(),
        "certificate": periodic["certificate"],
        "cusip": description[1],
        "equity_description_sha256": hashlib.sha256(description[0].encode()).hexdigest(),
    }
