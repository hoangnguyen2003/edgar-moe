"""Bind an issuer's listed ordinary shares to the same instrument in Form N-PORT."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
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

NS = "{http://www.sec.gov/edgar/nport}"


def _one[T](values: list[T], reason: str) -> T:
    if len(values) != 1:
        raise IdentityEvidenceError(reason)
    return values[0]


def _text(node: Tag) -> str:
    return " ".join(node.stripped_strings).replace("\xa0", " ")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _available(accepted: datetime, cutoff: datetime) -> datetime:
    available = accepted.astimezone(UTC) + timedelta(days=PUBLICATION_LAG_DAYS)
    if available > cutoff:
        raise IdentityEvidenceError("listed_isin_source_unavailable_at_cutoff")
    return available


def valid_isin(value: str) -> bool:
    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", value) is None:
        return False
    digits = "".join(str(int(char, 36)) for char in value)
    return (
        sum(
            sum(divmod(int(digit) * (2 if i % 2 else 1), 10))
            for i, digit in enumerate(reversed(digits))
        )
        % 10
        == 0
    )


def listing(
    index_raw: bytes,
    current_raw: bytes,
    exhibit_raw: bytes,
    reference: dict[str, str],
    periodic: dict[str, Any],
    *,
    issuer_name: str,
    cutoff: datetime,
) -> dict[str, Any]:
    """Authenticate the accession, exact linked exhibit, class value and listing symbol."""
    index = _soup(index_raw)
    current = _soup(current_raw)
    cik, accession = periodic["issuer_cik"], reference["accession"]
    path = f"/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    group = _flat(_text(_one(index.select(".companyInfo"), "listed_isin_index_filer_count")))
    filer = re.match(r"(.+?) \(Filer\) CIK\s*:\s*([0-9]{10})\b", group)
    if (
        filer is None
        or filer[1].casefold() != issuer_name.casefold()
        or filer[2] != cik
        or _flat(index.get_text(" ", strip=True)).count("SEC Accession No. " + accession) != 1
        or "Form 8-K - Current report" not in _flat(index.get_text(" ", strip=True))
    ):
        raise IdentityEvidenceError("listed_isin_index_identity")
    for name, form in ((reference["primary"], "8-K"), (reference["exhibit"], "EX-99.1")):
        link = _one(index.find_all("a", href=path + name), "listed_isin_index_document_link")
        row = link.find_parent("tr")
        cols = [_flat(_text(cell)) for cell in row.find_all("td", recursive=False)] if row else []
        if len(cols) != 5 or cols[2:4] != [name, form]:
            raise IdentityEvidenceError("listed_isin_index_document_type")
    exhibit_link = _one(
        [
            link
            for link in current.find_all("a")
            if link.get("href") in {reference["exhibit"], path + reference["exhibit"]}
        ],
        "listed_isin_issuer_report_exhibit_link",
    )
    exhibit_row = exhibit_link.find_parent("tr")
    if exhibit_row is None or not _flat(_text(exhibit_row)).startswith(
        "99.1 European Listing Prospectus,"
    ):
        raise IdentityEvidenceError("listed_isin_issuer_report_exhibit_description")
    dates = {}
    for header in index.select(".infoHead"):
        value = header.find_next_sibling("div")
        key = _flat(_text(header))
        if key in {"Accepted", "Filing Date"} and value is not None:
            if key in dates:
                raise IdentityEvidenceError("listed_isin_duplicate_index_date")
            dates[key] = _flat(_text(value))
    accepted = datetime.strptime(dates.get("Accepted", ""), "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=ZoneInfo("America/New_York")
    )
    if accepted.date().isoformat() != dates.get("Filing Date"):
        raise IdentityEvidenceError("listed_isin_index_dates")
    available = _available(accepted, cutoff)
    if available >= instant(periodic["available_by"]):
        raise IdentityEvidenceError("listed_isin_listing_after_periodic")

    class_title = re.fullmatch(
        r"Ordinary shares \((€[0-9]+(?:\.[0-9]+)?) nominal value per share\)",
        periodic["security_title"],
        re.I,
    )
    period_symbol = periodic["symbol_at_cutoff"]
    current_text = _flat(current.get_text(" ", strip=True))
    if (
        class_title is None
        or periodic["exchange_at_filing"] != "New York Stock Exchange"
        or not re.search(
            rf"one ordinary share, nominal value {re.escape(class_title[1])} per share, of "
            rf"{re.escape(issuer_name)} \(the “{re.escape(issuer_name)} Shares”\)",
            current_text,
            re.I,
        )
        or not re.search(
            rf"The {re.escape(issuer_name)} Shares will trade on the New York Stock Exchange "
            rf"\(“NYSE”\) and the Frankfurt Stock Exchange \(“FSE”\) under the ticker symbol "
            rf"“{re.escape(period_symbol)}”\.",
            current_text,
            re.I,
        )
    ):
        raise IdentityEvidenceError("listed_isin_issuer_registered_class_mismatch")

    # The archived prospectus is large. Read only the explicitly labelled
    # registration paragraph and its identifier paragraphs, capped at 4 KiB.
    if not 0 < len(exhibit_raw) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("listed_isin_exhibit_size")
    introduction = re.escape(
        f"When admitted to trading, the {issuer_name} Shares will be registered with:".encode()
    )
    start = _one(
        list(re.finditer(introduction, exhibit_raw, re.I)), "listed_isin_prospectus_section_count"
    ).start()
    matches = re.match(
        rb"[^<]+</p>\s*(?:(?!</t[dr]>)[\s\S]){1,3900}?</td>",
        exhibit_raw[start : start + 4096],
        re.I,
    )
    if matches is None:
        raise IdentityEvidenceError("listed_isin_prospectus_section_bound")
    facts = _flat(" ".join(_soup(matches[0]).stripped_strings))
    match = re.fullmatch(
        rf"When admitted to trading, the {re.escape(issuer_name)} Shares will be registered with:\s*"
        rf"International Securities Identification Number \(ISIN\): ([A-Z0-9]{{12}});\s*"
        rf"German Securities Identification Number \(\s*Wertpapierkennnummer,\s*WKN\): [A-Z0-9 ]+;\s*"
        rf"Ticker Symbol: {re.escape(period_symbol)}\.",
        facts,
        re.I,
    )
    if match is None or not valid_isin(match[1]):
        raise IdentityEvidenceError("listed_isin_prospectus_identifier_mismatch")
    return {
        "accession": accession,
        "available_by": available.isoformat(),
        "issuer_name": issuer_name,
        "isin": match[1],
        "index_sha256": hashlib.sha256(index_raw).hexdigest(),
        "issuer_report_sha256": hashlib.sha256(current_raw).hexdigest(),
        "prospectus_sha256": hashlib.sha256(exhibit_raw).hexdigest(),
    }


def nport_identity(
    raw: bytes, reference: dict[str, str], listed: dict[str, Any], *, cutoff: datetime
) -> dict[str, Any]:
    """Require the same ISIN and issuer on an unrestricted, long, direct equity row."""
    if not 0 < len(raw) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("listed_isin_nport_size")
    header = _one(
        re.findall(rb"<SEC-HEADER>(.*?)</SEC-HEADER>", raw, re.S), "listed_isin_nport_header"
    )

    def field(label: bytes) -> str:
        values: list[bytes] = re.findall(
            rb"(?m)^[ \t]*" + label + rb"[ \t]*:[ \t]*([^\r\n]+)", header
        )
        return _one(values, "listed_isin_nport_header_field").decode("ascii").strip()

    if (
        field(b"ACCESSION NUMBER") != reference["accession"]
        or field(b"CONFORMED SUBMISSION TYPE") != "NPORT-P"
        or field(b"CENTRAL INDEX KEY") != reference["filer_cik"]
        or field(b"PUBLIC DOCUMENT COUNT") != "1"
    ):
        raise IdentityEvidenceError("listed_isin_nport_header_identity")
    accepted_raw = _one(
        re.findall(rb"(?m)^<ACCEPTANCE-DATETIME>([0-9]{14})[ \t\r]*$", header),
        "listed_isin_nport_acceptance",
    )
    accepted = datetime.strptime(accepted_raw.decode(), "%Y%m%d%H%M%S").replace(
        tzinfo=ZoneInfo("America/New_York")
    )
    period = datetime.strptime(field(b"CONFORMED PERIOD OF REPORT"), "%Y%m%d").date()
    if (
        accepted.date().strftime("%Y%m%d") != field(b"FILED AS OF DATE")
        or not 0 <= (accepted.date() - period).days <= 90
        or (cutoff.date() - period).days > 365
        or period < instant(listed["available_by"]).date()
    ):
        raise IdentityEvidenceError("listed_isin_nport_report_date")
    available = _available(accepted, cutoff)
    document = _one(
        re.findall(rb"<DOCUMENT>(.*?)</DOCUMENT>", raw, re.S), "listed_isin_nport_document"
    )
    if not re.search(rb"(?m)^<TYPE>NPORT-P\r?$", document) or not re.search(
        rb"(?m)^<FILENAME>primary_doc\.xml\r?$", document
    ):
        raise IdentityEvidenceError("listed_isin_nport_document_identity")
    xml = _one(re.findall(rb"<XML>(.*?)</XML>", document, re.S), "listed_isin_nport_xml").strip()
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.I):
        raise IdentityEvidenceError("listed_isin_nport_xml_entities")
    try:
        tree = ET.fromstring(xml)
    except ET.ParseError as error:
        raise IdentityEvidenceError("listed_isin_nport_invalid_xml") from error

    def value(element: ET.Element, *path: str) -> str:
        found = _one(
            element.findall("/".join(NS + part for part in path)), "listed_isin_nport_xml_field"
        )
        if not found.text or len(found):
            raise IdentityEvidenceError("listed_isin_nport_xml_field")
        return _flat(found.text)

    if (
        tree.tag != NS + "edgarSubmission"
        or value(tree, "headerData", "submissionType") != "NPORT-P"
        or value(tree, "headerData", "isConfidential") != "false"
        or value(tree, "formData", "genInfo", "regCik") != reference["filer_cik"]
        or value(tree, "formData", "genInfo", "repPdDate") != period.isoformat()
        or value(tree, "formData", "genInfo", "repPdEnd") != period.isoformat()
    ):
        raise IdentityEvidenceError("listed_isin_nport_xml_identity")
    rows = tree.findall(f"{NS}formData/{NS}invstOrSecs/{NS}invstOrSec")
    relevant = _one(
        [
            row
            for row in rows
            if any(
                child.get("value") == listed["isin"]
                for child in row.findall(f"{NS}identifiers/{NS}isin")
            )
        ],
        "listed_isin_nport_equity_row_count",
    )
    isin_field = _one(relevant.findall(f"{NS}identifiers/{NS}isin"), "listed_isin_nport_isin_count")
    cusip = value(relevant, "cusip")
    if (
        isin_field.attrib != {"value": listed["isin"]}
        or value(relevant, "name").casefold() != listed["issuer_name"].casefold()
        or class_signature(value(relevant, "title")) != ("ordinary", "", "")
        or not valid_cusip(cusip)
        or any(
            value(relevant, name) != expected
            for name, expected in (
                ("assetCat", "EC"),
                ("units", "NS"),
                ("payoffProfile", "Long"),
                ("isRestrictedSec", "N"),
            )
        )
        or relevant.find(NS + "derivativeInfo") is not None
    ):
        raise IdentityEvidenceError("listed_isin_nport_equity_class_conflict")
    return {
        "accession": reference["accession"],
        "available_by": available.isoformat(),
        "period_end": period.isoformat(),
        "document_sha256": hashlib.sha256(raw).hexdigest(),
        "cusip": cusip,
        "isin": listed["isin"],
    }
