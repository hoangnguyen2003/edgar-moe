"""Read an exact ordinary-share identifier from a dated issuer offering table."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .historical_identity import (
    MAX_DOCUMENT_BYTES,
    PUBLICATION_LAG_DAYS,
    IdentityEvidenceError,
    _soup,
    instant,
    valid_cusip,
)
from .historical_listed_isin import _flat, _one, valid_isin


def ordinary_share_offering(
    raw: bytes,
    reference: dict[str, str],
    periodic: dict[str, Any],
    *,
    issuer_name: str,
    cutoff: datetime,
) -> dict[str, str]:
    if not 0 < len(raw) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("offering_identity_file_size")
    header = _one(
        re.findall(rb"<SEC-HEADER>(.*?)</SEC-HEADER>", raw, re.S), "offering_identity_header"
    )

    def field(label: bytes) -> str:
        values: list[bytes] = re.findall(rb"(?m)^[ \t]*" + label + rb":[ \t]*([^\r\n]+)", header)
        return _one(values, "offering_identity_header_field").decode("ascii").strip()

    cik = reference["filer_cik"]
    if (
        cik != periodic["issuer_cik"]
        or field(b"ACCESSION NUMBER") != reference["accession"]
        or field(b"CONFORMED SUBMISSION TYPE") != "FWP"
        or field(b"PUBLIC DOCUMENT COUNT") != "1"
    ):
        raise IdentityEvidenceError("offering_identity_filing")
    for label in (rb"SUBJECT COMPANY", rb"FILED BY"):
        group = _one(
            re.findall(rb"(?ms)^" + label + rb":[ \t]*\r?\n(.*?)(?=^\S|\Z)", header),
            "offering_identity_role",
        )
        if re.findall(rb"(?m)^[ \t]*CENTRAL INDEX KEY:[ \t]*([0-9]{10})", group) != [cik.encode()]:
            raise IdentityEvidenceError("offering_identity_filer_cik")
    accepted_raw = _one(
        re.findall(rb"(?m)^<ACCEPTANCE-DATETIME>([0-9]{14})[ \t\r]*$", header),
        "offering_identity_acceptance",
    )
    local = datetime.strptime(accepted_raw.decode(), "%Y%m%d%H%M%S").replace(
        tzinfo=ZoneInfo("America/New_York")
    )
    accepted_at = local.astimezone(UTC)
    available = accepted_at + timedelta(days=PUBLICATION_LAG_DAYS)
    if (
        local.strftime("%Y%m%d") != field(b"FILED AS OF DATE")
        or available > cutoff
        or available >= instant(periodic["available_by"])
        or (cutoff - accepted_at).days > 1827
    ):
        raise IdentityEvidenceError("offering_identity_source_date")
    primary = _one(
        re.findall(rb"<DOCUMENT>(.*?)</DOCUMENT>", raw, re.S), "offering_identity_document"
    )
    if not re.search(rb"(?m)^<TYPE>FWP[ \t\r]*$", primary):
        raise IdentityEvidenceError("offering_identity_primary_type")
    html = _one(re.findall(rb"<TEXT>(.*?)</TEXT>", primary, re.S), "offering_identity_primary_text")
    soup = _soup(html)
    par = re.fullmatch(
        r"Ordinary Shares, (\$[0-9]+(?:\.[0-9]+)?) par value per share",
        periodic["security_title"],
        re.I,
    )
    if (
        par is None
        or periodic["exchange_at_filing"] != "New York Stock Exchange"
        or re.search(
            rf"\b[0-9][0-9,]* Ordinary Shares, par value {re.escape(par[1])} per Share "
            r"\(the “Ordinary Shares”\)",
            _flat(soup.get_text(" ", strip=True)),
            re.I,
        )
        is None
    ):
        raise IdentityEvidenceError("offering_identity_periodic_class")
    records = []
    ordinary_table = None
    for row in soup.find_all("tr"):
        vals = [
            text
            for cell in row.find_all("td", recursive=False)
            if (text := _flat(cell.get_text(" ", strip=True)))
        ]
        if len(vals) == 2:
            records.append((vals[0], vals[1]))
        if len(vals) == 1 and vals[0] == "Terms Applicable to the Ordinary Shares Offering":
            if ordinary_table is not None:
                raise IdentityEvidenceError("offering_identity_ambiguous_ordinary_table")
            ordinary_table = row.find_parent("table")
    ticker = periodic["symbol_at_cutoff"]
    if (
        [v.casefold() for k, v in records if k == "Issuer:"] != [issuer_name.casefold()]
        or [v for k, v in records if k == "Ticker / Exchange for the Ordinary Shares:"]
        != [f"{ticker} / The New York Stock Exchange (“ NYSE ”)."]
        or ordinary_table is None
    ):
        raise IdentityEvidenceError("offering_identity_ticker_issuer_or_table")
    identifier_rows = []
    for row in ordinary_table.find_all("tr"):
        cells = [
            text
            for cell in row.find_all("td", recursive=False)
            if (text := _flat(cell.get_text(" ", strip=True)))
        ]
        if cells and cells[0] == "CUSIP / ISIN for the Ordinary Shares:":
            identifier_rows.append(cells[1:])
    identifiers = _one(identifier_rows, "offering_identity_identifier_row")
    pair = re.fullmatch(
        r"([0-9A-Z]{6}) ?([0-9A-Z]{2}[0-9]) / ([A-Z0-9]{12})",
        _one(identifiers, "offering_identity_identifier_cell"),
    )
    if pair is None or not valid_cusip(pair[1] + pair[2]) or not valid_isin(pair[3]):
        raise IdentityEvidenceError("offering_identity_identifier_conflict")
    return {
        "accession": reference["accession"],
        "available_by": available.isoformat(),
        "document_sha256": hashlib.sha256(raw).hexdigest(),
        "cusip": pair[1] + pair[2],
        "isin": pair[3],
    }
