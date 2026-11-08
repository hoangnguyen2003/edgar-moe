"""Explicit voting rights in a dated, incorporated single-class charter.

No class is equated to another class. This supplies only a missing voting
qualifier for the sole authorized common-stock class described by the source.
It is not evidence of uninterrupted security or historical market continuity.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from bs4 import BeautifulSoup

from edgar_moe.data.historical_identity import (
    MAX_DOCUMENT_BYTES,
    MAX_RETAINED_DOCUMENT_BYTES,
    IdentityEvidenceError,
    class_signature,
    instant,
)


def _name(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def _date(value: str) -> str:
    try:
        return datetime.strptime(value, "%B %d, %Y").date().isoformat()
    except ValueError as error:
        raise IdentityEvidenceError("invalid_governing_reference_date") from error


def governing_reference(payload: bytes) -> dict[str, str] | None:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_RETAINED_DOCUMENT_BYTES:
        raise IdentityEvidenceError("invalid_governing_index_bytes")
    soup = BeautifulSoup(payload, "html.parser")
    rows = []
    for row in soup.find_all("tr"):
        if row.find("tr") is not None:
            continue
        text = " ".join(row.get_text(" ", strip=True).split())
        if re.search(r"(?:articles|certificate) of (?:incorporation|amendment)", text, re.I):
            rows.append((row, text))
    if len(rows) > 1:
        raise IdentityEvidenceError("ambiguous_governing_index_references")
    if not rows:
        return None
    row, text = rows[0]
    dates = r"[A-Za-z]+ [0-9]{1,2}, [0-9]{4}"
    match = re.fullmatch(
        r"3\.[0-9]+\s+Amended and Restated Articles of Incorporation of (.+?), "
        rf"reflecting all amendments thereto through ({dates}), incorporated herein "
        r"by reference from Exhibit (3\.[0-9]+[a-z]?) to our Form (10-Q|10-K) "
        rf"filed ({dates})\.",
        text,
        re.I,
    )
    if match is None:
        return None
    links = row.find_all("a", href=True)
    if len(links) != 1:
        raise IdentityEvidenceError("ambiguous_governing_index_links")
    through, filed = _date(match[2]), _date(match[5])
    if through > filed:
        raise IdentityEvidenceError("governing_amendment_after_filing")
    return {
        "href": str(links[0]["href"]),
        "reported_issuer_name": match[1],
        "amendments_through": through,
        "referenced_exhibit": match[3].upper(),
        "referenced_form": match[4].upper(),
        "referenced_filing_date": filed,
        "index_description_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def _par_value(title: str) -> Decimal:
    values = re.findall(r"\$\s*([0-9]*(?:\.[0-9]+)?)", title)
    if len(values) != 1 or not values[0]:
        raise IdentityEvidenceError("missing_or_ambiguous_governing_par_value")
    return Decimal(values[0])


def governing_voting_evidence(
    payload: bytes,
    *,
    cik: str,
    accession: str,
    filename: str,
    reference: dict[str, str],
    expected_title: str,
    cutoff: datetime,
) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("invalid_governing_submission_bytes")
    if (
        re.fullmatch(r"[0-9]{10}", cik) is None
        or int(cik) == 0
        or re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession) is None
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.html?", filename) is None
    ):
        raise IdentityEvidenceError("invalid_governing_source_identity")
    if class_signature(expected_title) != ("common", "", "voting"):
        raise IdentityEvidenceError("unsupported_governing_registered_class")
    headers = re.findall(rb"<SEC-HEADER>(.*?)</SEC-HEADER>", payload, re.S | re.I)
    if len(headers) != 1 or len(headers[0]) > 256 * 1024:
        raise IdentityEvidenceError("ambiguous_governing_SEC_header")
    header = headers[0].replace(b"\r\n", b"\n")
    if re.findall(rb"(?m)^ACCESSION NUMBER:[ \t]*([^\n]+)", header) != [accession.encode()]:
        raise IdentityEvidenceError("governing_accession_mismatch")
    forms = re.findall(rb"(?m)^CONFORMED SUBMISSION TYPE:[ \t]*([^\n]+)", header)
    if forms != [reference["referenced_form"].encode()] or forms[0] not in {b"10-Q", b"10-K"}:
        raise IdentityEvidenceError("governing_source_form_mismatch")
    filers = re.findall(rb"(?ms)^FILER:\s*\n(.*?)(?=^\S|\Z)", header)
    if len(filers) != 1:
        raise IdentityEvidenceError("ambiguous_governing_filer")
    identifiers = re.findall(rb"CENTRAL INDEX KEY:[ \t]*([0-9]+)", filers[0])
    names = re.findall(rb"COMPANY CONFORMED NAME:[ \t]*([^\n]+)", filers[0])
    if len(identifiers) != 1 or identifiers[0].decode().zfill(10) != cik or len(names) != 1:
        raise IdentityEvidenceError("governing_filer_issuer_mismatch")
    try:
        name = names[0].decode("utf-8")
        filed = re.findall(rb"(?m)^FILED AS OF DATE:[ \t]*([0-9]{8})[ \t]*$", header)
        accepted = re.findall(rb"<ACCEPTANCE-DATETIME>([0-9]{14})", header)
        if len(filed) != 1 or len(accepted) != 1:
            raise ValueError("missing dates")
        filing_date = datetime.strptime(filed[0].decode(), "%Y%m%d").date()
        acceptance_date = datetime.strptime(accepted[0].decode(), "%Y%m%d%H%M%S").date()
    except (UnicodeDecodeError, ValueError) as error:
        raise IdentityEvidenceError("invalid_governing_source_dates_or_name") from error
    available = datetime.combine(filing_date + timedelta(days=14), time.min, UTC)
    if (
        not _name(name)
        or _name(name) != _name(reference["reported_issuer_name"])
        or filing_date.isoformat() != reference["referenced_filing_date"]
        or acceptance_date > filing_date
        or cutoff.utcoffset() is None
        or available > cutoff
    ):
        raise IdentityEvidenceError("governing_reference_issuer_or_date_mismatch")
    documents = re.findall(rb"<DOCUMENT>(.*?)</DOCUMENT>", payload, re.S | re.I)
    if not 1 <= len(documents) <= 128:
        raise IdentityEvidenceError("invalid_governing_document_set")
    matches = [
        doc
        for doc in documents
        if re.findall(rb"(?im)^<FILENAME>[ \t]*([^\r\n]+)", doc) == [filename.encode()]
    ]
    if len(matches) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_governing_exhibit")
    doc = matches[0]
    types = re.findall(rb"(?im)^<TYPE>[ \t]*([^\r\n]+)", doc)
    if len(types) != 1:
        raise IdentityEvidenceError("ambiguous_governing_exhibit_type")
    exhibit_type = re.fullmatch(rb"EX-(3\.[0-9]+)(?:([A-Z])|\(([A-Z])\))?", types[0].upper())
    # SEC can spell the same exhibit number 3.1(E) or 3.1E.
    if exhibit_type is None:
        raise IdentityEvidenceError("governing_exhibit_type_mismatch")
    exhibit = (exhibit_type[1] + (exhibit_type[2] or exhibit_type[3] or b"")).decode()
    if exhibit != reference["referenced_exhibit"]:
        raise IdentityEvidenceError("governing_exhibit_type_mismatch")
    texts = re.findall(rb"<TEXT>(.*?)</TEXT>", doc, re.S | re.I)
    if len(texts) != 1:
        raise IdentityEvidenceError("ambiguous_governing_exhibit_text")
    text = " ".join(BeautifulSoup(texts[0], "html.parser").get_text(" ", strip=True).split())
    legal_names = re.findall(
        r"\bARTICLE I NAME The name of the Corporation is (.+?)\s+ARTICLE II\b", text, re.I
    )
    articles = re.findall(r"\bARTICLE IV AUTHORIZED SHARES (.*?)\bARTICLE V\b", text, re.I)
    if (
        len(legal_names) != 1
        or _name(legal_names[0]) != _name(name)
        or len(articles) != 1
        or len(re.findall(r"\bARTICLE IV\b", text, re.I)) != 1
        or len(re.findall(r"\bauthority to issue\b", text, re.I)) != 1
        or len(re.findall(r"\bcommon (?:stock|shares)\b", text, re.I))
        != len(re.findall(r"\bcommon (?:stock|shares)\b", articles[0], re.I))
        or re.search(r"\bpreferred\b|\bnon[ -]?voting\b|\bClass\s+[A-Z0-9]+\b", text, re.I)
    ):
        raise IdentityEvidenceError("ambiguous_governing_authorized_classes")
    rights = re.fullmatch(
        r"The total number of shares of capital stock which the Corporation has "
        r"authority to issue is ([1-9][0-9]*(?:,[0-9]{3})*) shares of common stock, "
        r"par value (\$[0-9]+(?:\.[0-9]+)?) per share \([“\"]Common Stock[”\"]\)\. "
        r"The holders of the Common Stock shall be entitled to one \(1\) vote per "
        r"share on all matters to be voted on by the Corporation[’']s shareholders\. "
        r"As and when dividends are declared or paid, the holders of Common Stock shall "
        r"be entitled to participate in such dividends ratably on a per share basis\. "
        r"The holders of the Common Stock shall be entitled to participate ratably on a "
        r"per share basis in all distributions to the holders (?:of|to) the Common Stock "
        r"in any liquidation, dissolution or winding up of the Corporation\.",
        articles[0].strip(),
        re.I,
    )
    if rights is None or _par_value(rights[2]) != _par_value(expected_title):
        raise IdentityEvidenceError("missing_or_conflicting_governing_common_voting_rights")
    return {
        "issuer_cik": cik,
        "accession": accession,
        "filename": filename,
        "reported_filing_date": filing_date.isoformat(),
        "reported_local_acceptance_datetime": accepted[0].decode(),
        "available_by": available.isoformat(),
        "availability_basis": "SEC_header_filing_date_plus_fourteen_calendar_days_date_only",
        "security_title": expected_title,
        "authorized_common_shares": rights[1],
        "reported_par_value": rights[2],
        "votes_per_share": 1,
        "complete_submission_sha256": hashlib.sha256(payload).hexdigest(),
        "SEC_header_sha256": hashlib.sha256(headers[0]).hexdigest(),
        "document_sha256": hashlib.sha256(texts[0]).hexdigest(),
        "authorized_article_sha256": hashlib.sha256(articles[0].encode()).hexdigest(),
        "subject_name_sha256": hashlib.sha256(names[0]).hexdigest(),
    }


def qualify_ownership_voting(
    periodic: dict[str, Any], ownership: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    if (
        class_signature(periodic["security_title"]) != ("common", "", "voting")
        or class_signature(ownership["security_title"]) != ("common", "", "")
        or class_signature(evidence["security_title"]) != ("common", "", "voting")
        or len({periodic["issuer_cik"], ownership["issuer_cik"], evidence["issuer_cik"]}) != 1
        or instant(evidence["available_by"]) > instant(ownership["accepted_at"])
        or evidence["votes_per_share"] != 1
        or _par_value(periodic["security_title"]) != _par_value(evidence["reported_par_value"])
        or (
            "$" in ownership["security_title"]
            and _par_value(ownership["security_title"])
            != _par_value(evidence["reported_par_value"])
        )
    ):
        raise IdentityEvidenceError("unbound_governing_ownership_qualifier")
    return {
        **ownership,
        "security_title": "Voting " + ownership["security_title"],
        "reported_security_title": ownership["security_title"],
        "class_qualifier_basis": "explicit_single_authorized_common_class_and_one_vote_per_share",
    }
