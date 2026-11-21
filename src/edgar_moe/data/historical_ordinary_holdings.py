"""Verify the ordinary-share class named in dated, original Form 13F reports."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .historical_holdings_class import FORM_NS, TABLE_NS, _one, _xml
from .historical_identity import (
    MAX_DOCUMENT_BYTES,
    MAX_RETAINED_DOCUMENT_BYTES,
    IdentityEvidenceError,
    _soup,
    class_signature,
    valid_cusip,
)
from .historical_listed_isin import _one as single


def registrant_identity(payload: bytes, cik: str) -> tuple[str, str]:
    soup = _soup(payload, MAX_RETAINED_DOCUMENT_BYTES)
    context_ids: set[str] = set()
    seen: set[str] = set()
    for context in soup.find_all("xbrli:context"):
        context_id = context.get("id")
        if not isinstance(context_id, str) or not context_id:
            continue
        if context_id in seen:
            raise IdentityEvidenceError("ordinary_holdings_registrant_context")
        seen.add(context_id)
        identifiers = context.find_all("xbrli:identifier")
        if (
            len(identifiers) == 1
            and identifiers[0].get("scheme")
            in {"http://www.sec.gov/CIK", "https://www.sec.gov/CIK"}
            and identifiers[0].get_text(strip=True) == cik
        ):
            context_ids.add(context_id)
    names, countries = set(), set()
    for fact in soup.find_all("ix:nonnumeric"):
        if fact.get("contextref") not in context_ids:
            continue
        name = str(fact.get("name", "")).lower()
        value = " ".join(fact.get_text(" ", strip=True).upper().split())
        if name == "dei:entityregistrantname":
            names.add(value)
        if name == "dei:entityincorporationstatecountrycode":
            countries.add(value)
    if len(names) != 1 or "" in names or len(countries) > 1 or "" in countries:
        raise IdentityEvidenceError("ordinary_holdings_registrant_identity")
    return next(iter(names)), next(iter(countries), "")


def _class_matches(
    name: str, title: str, *, issuer: str, country: str, registered: str, share_class: str
) -> bool:
    name, title, issuer = (" ".join(value.upper().split()) for value in (name, title, issuer))
    country_suffix = rf"(?: {re.escape(country)} SHS)?" if country else ""
    display = re.fullmatch(
        re.escape(issuer) + country_suffix + r"(?:[ -](?:CLASS|CL) ([A-Z0-9]+))?", name
    )
    heading = re.fullmatch(
        r"(?:(?:CLASS|CL) ([A-Z0-9]+) )?"
        r"(?:ORDINARY (?:SHARE|SHARES|SHS)|ORD|COMMON\s*/\s*ORDINARY(?: STOCK)?)"
        r"(?:\s*(?:USD|\$)\s*((?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)))?",
        title,
    )
    if display is None or heading is None:
        return False
    labels = {label for label in (display[1], heading[1]) if label}
    if labels != ({share_class} if share_class else set()):
        return False
    if heading[2] is not None:
        par = re.findall(r"\$\s*([0-9]+(?:\.[0-9]+)?|\.[0-9]+)", registered)
        if len(par) != 1 or Decimal(par[0]) != Decimal(heading[2]):
            return False
    # The short form ORD is accepted only when the issuer's nominal value also agrees.
    return title != "ORD" and (re.search(r"\bORD\b", title) is None or heading[2] is not None)


def filed_ordinary_holding(
    payload: bytes,
    reference: dict[str, str],
    *,
    issuer_name: str,
    issuer_country: str = "",
    registered_title: str,
    cusip: str,
    cutoff: datetime,
) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("ordinary_holdings_document_size")
    family, share_class, rights = class_signature(registered_title)
    if family != "ordinary" or rights or not valid_cusip(cusip):
        raise IdentityEvidenceError("ordinary_holdings_target_class")
    header = single(
        re.findall(rb"<SEC-HEADER>(.*?)</SEC-HEADER>", payload, re.S),
        "ordinary_holdings_header",
    )
    if len(header) > 256 * 1024:
        raise IdentityEvidenceError("ordinary_holdings_header_size")

    def field(label: bytes) -> str:
        values: list[bytes] = re.findall(rb"(?m)^[ \t]*" + label + rb":[ \t]*([^\r\n]+)", header)
        return single(values, "ordinary_holdings_header_field").decode("ascii").strip()

    if (
        field(b"ACCESSION NUMBER") != reference["accession"]
        or field(b"CONFORMED SUBMISSION TYPE") != "13F-HR"
        or field(b"CENTRAL INDEX KEY") != reference["manager_cik"]
    ):
        raise IdentityEvidenceError("ordinary_holdings_filing_identity")
    accepted_raw = single(
        re.findall(rb"(?m)^<ACCEPTANCE-DATETIME>([0-9]{14})[ \t\r]*$", header),
        "ordinary_holdings_acceptance",
    )
    accepted_local = datetime.strptime(accepted_raw.decode(), "%Y%m%d%H%M%S").replace(
        tzinfo=ZoneInfo("America/New_York")
    )
    period = datetime.strptime(field(b"CONFORMED PERIOD OF REPORT"), "%Y%m%d").date()
    filed = datetime.strptime(field(b"FILED AS OF DATE"), "%Y%m%d").date()
    available = datetime.combine(filed + timedelta(days=14), time.min, UTC)
    if (
        not period <= accepted_local.date() <= filed
        or (filed - accepted_local.date()).days > 3
        or (cutoff.date() - period).days > 365
        or accepted_local.astimezone(UTC) > cutoff
        or available > cutoff
    ):
        raise IdentityEvidenceError("ordinary_holdings_availability")
    documents = re.findall(rb"<DOCUMENT>(.*?)</DOCUMENT>", payload, re.S)
    if len(documents) != 2:
        raise IdentityEvidenceError("ordinary_holdings_document_set")
    primary = single(
        [doc for doc in documents if re.search(rb"(?m)^<TYPE>13F-HR[ \t\r]*$", doc)],
        "ordinary_holdings_primary",
    )
    information = single(
        [doc for doc in documents if re.search(rb"(?m)^<TYPE>INFORMATION TABLE[ \t\r]*$", doc)],
        "ordinary_holdings_information_doc",
    )
    cover, _ = _xml(primary, FORM_NS, "edgarSubmission")
    cover_ns = "{" + FORM_NS + "}"

    def cover_field(*path: str) -> str:
        return _one(cover, "/".join(cover_ns + p for p in path))

    amendments = cover.findall(
        "/".join(cover_ns + part for part in ("formData", "coverPage", "isAmendment"))
    )
    if amendments and cover_field("formData", "coverPage", "isAmendment") != "false":
        raise IdentityEvidenceError("ordinary_holdings_amendment")
    period_text = period.strftime("%m-%d-%Y")
    if (
        cover_field("headerData", "submissionType") != "13F-HR"
        or cover_field("headerData", "filerInfo", "liveTestFlag") != "LIVE"
        or cover_field("headerData", "filerInfo", "filer", "credentials", "cik")
        != reference["manager_cik"]
        or cover_field("headerData", "filerInfo", "periodOfReport") != period_text
        or cover_field("formData", "coverPage", "reportCalendarOrQuarter") != period_text
        or cover_field("formData", "coverPage", "reportType") != "13F HOLDINGS REPORT"
        or cover_field("formData", "summaryPage", "isConfidentialOmitted") != "false"
    ):
        raise IdentityEvidenceError("ordinary_holdings_cover_identity")
    tables, xml = _xml(information, TABLE_NS, "informationTable")
    ns = "{" + TABLE_NS + "}"
    if (
        cover_field("formData", "summaryPage", "tableEntryTotal") != str(len(tables))
        or not 1 <= len(tables) <= 15000
        or any(row.tag != ns + "infoTable" for row in tables)
    ):
        raise IdentityEvidenceError("ordinary_holdings_table_count")
    matches = []
    for row in tables:
        if not any((node.text or "").strip() == cusip for node in row.findall(ns + "cusip")):
            continue
        if _one(row, ns + "cusip") != cusip:
            raise IdentityEvidenceError("ordinary_holdings_cusip")
        options = row.findall(ns + "putCall")
        if options:
            if len(options) != 1 or options[0].text not in {"Put", "Call"}:
                raise IdentityEvidenceError("ordinary_holdings_derivative_type")
            continue
        title = _one(row, ns + "titleOfClass")
        name = _one(row, ns + "nameOfIssuer")
        if (
            _one(row, ns + "shrsOrPrnAmt/" + ns + "sshPrnamtType") != "SH"
            or not re.fullmatch(r"[1-9][0-9]*", _one(row, ns + "shrsOrPrnAmt/" + ns + "sshPrnamt"))
            or not _class_matches(
                name,
                title,
                issuer=issuer_name,
                country=issuer_country,
                registered=registered_title,
                share_class=share_class,
            )
        ):
            raise IdentityEvidenceError("ordinary_holdings_issuer_or_class_conflict")
        matches.append({"reported_issuer_name": name, "reported_title": title})
    if not matches:
        raise IdentityEvidenceError("ordinary_holdings_missing_equity")
    return {
        "accession": reference["accession"],
        "manager_cik": reference["manager_cik"],
        "document_sha256": hashlib.sha256(payload).hexdigest(),
        "information_table_sha256": hashlib.sha256(xml).hexdigest(),
        "report_period": period.isoformat(),
        "available_by": available.isoformat(),
        "rows": matches,
        "cusip": cusip,
    }
