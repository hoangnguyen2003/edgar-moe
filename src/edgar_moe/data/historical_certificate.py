"""Offline evidence from an explicitly incorporated stock-certificate exhibit.

This is a narrow specimen format, not a general security-description parser.
It establishes a reported historical identifier, not uninterrupted continuity.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, time, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from edgar_moe.data.historical_identity import (
    MAX_DOCUMENT_BYTES,
    MAX_RETAINED_DOCUMENT_BYTES,
    IdentityEvidenceError,
    class_signature,
    valid_cusip,
)


def certificate_reference(payload: bytes, expected_title: str) -> dict[str, str] | None:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_RETAINED_DOCUMENT_BYTES:
        raise IdentityEvidenceError("invalid_certificate_index_bytes")
    expected = class_signature(expected_title)
    soup = BeautifulSoup(payload, "html.parser")
    references: dict[str, str] = {}
    for row in soup.find_all("tr"):
        if row.find("tr") is not None:
            continue
        text = " ".join(row.get_text(" ", strip=True).split())
        if not re.search(r"\bspecimen\b", text, re.I) or not re.search(
            r"\bcertificate\b", text, re.I
        ):
            continue
        try:
            if class_signature(text) != expected:
                continue
        except IdentityEvidenceError:
            continue
        links = row.find_all("a", href=True)
        if len(links) != 1:
            raise IdentityEvidenceError("ambiguous_certificate_index_links")
        href = str(links[0].get("href"))
        references[href] = hashlib.sha256(text.encode()).hexdigest()
    if len(references) > 1:
        raise IdentityEvidenceError("ambiguous_certificate_index_references")
    if not references:
        return None
    href = next(iter(references))
    return {"href": href, "index_description_sha256": references[href]}


def certificate_location(href: str, parent_url: str, cik: str) -> dict[str, str]:
    if re.fullmatch(r"[0-9]{10}", cik) is None or int(cik) == 0:
        raise IdentityEvidenceError("invalid_certificate_issuer")
    url = urljoin(parent_url, href)
    parsed = urlsplit(url)
    match = re.fullmatch(
        rf"/Archives/edgar/data/{int(cik)}/([0-9]{{18}})/([A-Za-z0-9][A-Za-z0-9_.-]{{0,180}}\.html?)",
        parsed.path,
    )
    if (
        parsed.scheme not in {"https", "http"}
        or parsed.netloc != "www.sec.gov"
        or parsed.query
        or parsed.fragment
        or match is None
    ):
        raise IdentityEvidenceError("invalid_certificate_source_link")
    digits, filename = match.groups()
    return {
        "url": "https://www.sec.gov" + parsed.path,
        "accession": digits[:10] + "-" + digits[10:12] + "-" + digits[12:],
        "filename": filename,
    }


def registered_certificate_identity(
    payload: bytes,
    *,
    cik: str,
    accession: str,
    filename: str,
    expected_title: str,
    cutoff: datetime,
) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("invalid_certificate_submission_bytes")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.html?", filename) is None:
        raise IdentityEvidenceError("invalid_certificate_filename")
    headers = re.findall(rb"<SEC-HEADER>(.*?)</SEC-HEADER>", payload, re.S | re.I)
    if len(headers) != 1 or len(headers[0]) > 256 * 1024:
        raise IdentityEvidenceError("ambiguous_certificate_SEC_header")
    header = headers[0].replace(b"\r\n", b"\n")
    if re.findall(rb"(?m)^ACCESSION NUMBER:[ \t]*([^\n]+)", header) != [accession.encode()]:
        raise IdentityEvidenceError("certificate_accession_mismatch")
    forms = re.findall(rb"(?m)^CONFORMED SUBMISSION TYPE:[ \t]*([^\n]+)", header)
    if len(forms) != 1 or forms[0] not in {b"8-K", b"8-K12B"}:
        raise IdentityEvidenceError("unsupported_certificate_submission_form")
    filers = re.findall(rb"(?ms)^FILER:\s*\n(.*?)(?=^\S|\Z)", header)
    if len(filers) != 1:
        raise IdentityEvidenceError("ambiguous_certificate_filer")
    identifiers = re.findall(rb"CENTRAL INDEX KEY:[ \t]*([0-9]+)", filers[0])
    names = re.findall(rb"COMPANY CONFORMED NAME:[ \t]*([^\n]+)", filers[0])
    if (
        re.fullmatch(r"[0-9]{10}", cik) is None
        or len(identifiers) != 1
        or identifiers[0].decode().zfill(10) != cik
        or len(names) != 1
    ):
        raise IdentityEvidenceError("certificate_filer_issuer_mismatch")
    filed = re.findall(rb"(?m)^FILED AS OF DATE:[ \t]*([0-9]{8})[ \t]*$", header)
    accepted = re.findall(rb"<ACCEPTANCE-DATETIME>([0-9]{14})", header)
    if len(filed) != 1 or len(accepted) != 1:
        raise IdentityEvidenceError("missing_certificate_source_dates")
    try:
        filing_date = datetime.strptime(filed[0].decode(), "%Y%m%d").date()
        acceptance_date = datetime.strptime(accepted[0].decode(), "%Y%m%d%H%M%S").date()
    except ValueError as error:
        raise IdentityEvidenceError("invalid_certificate_source_dates") from error
    # Keep the reported local acceptance string without inventing its timezone.
    # Availability uses a separate, conservative date-only 14-calendar-day lag.
    available = datetime.combine(filing_date + timedelta(days=14), time.min, UTC)
    if cutoff.utcoffset() is None or acceptance_date > filing_date or available > cutoff:
        raise IdentityEvidenceError("certificate_not_available_by_index_date")
    documents = re.findall(rb"<DOCUMENT>(.*?)</DOCUMENT>", payload, re.S | re.I)
    if not 1 <= len(documents) <= 128:
        raise IdentityEvidenceError("invalid_certificate_document_set")
    matches = []
    for document in documents:
        filenames = re.findall(rb"(?im)^<FILENAME>[ \t]*([^\r\n]+)", document)
        if filenames == [filename.encode()]:
            matches.append(document)
    if len(matches) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_certificate_exhibit")
    document = matches[0]
    types = re.findall(rb"(?im)^<TYPE>[ \t]*([^\r\n]+)", document)
    if len(types) != 1 or re.fullmatch(rb"EX-4\.[0-9]{1,3}", types[0]) is None:
        raise IdentityEvidenceError("unsupported_certificate_exhibit_type")
    texts = re.findall(rb"<TEXT>(.*?)</TEXT>", document, re.S | re.I)
    if len(texts) != 1:
        raise IdentityEvidenceError("ambiguous_certificate_exhibit_text")
    text = " ".join(BeautifulSoup(texts[0], "html.parser").get_text(" ", strip=True).split())
    titles = re.findall(
        r"FULLY PAID AND NON[- ]ASSESSABLE SHARES OF ([^,]{1,160}),\s*PAR VALUE\b",
        text,
        re.I,
    )
    if len(titles) != 1 or class_signature(titles[0]) != class_signature(expected_title):
        raise IdentityEvidenceError("certificate_share_class_mismatch")

    def normalize(value: str) -> str:
        return " ".join(re.sub(r"[^A-Z0-9]", " ", value.upper()).split())

    issuer = normalize(names[0].decode("ascii"))
    if not issuer or issuer + " FULLY PAID AND NON ASSESSABLE SHARES OF " not in normalize(text):
        raise IdentityEvidenceError("certificate_face_issuer_mismatch")
    identifiers_text = re.findall(
        r"\bCUSIP\s+(?:NO\.?\s+|NUMBER\s+)?"
        r"([A-Z0-9]{6}\s+[A-Z0-9]{2}\s+[0-9]|[A-Z0-9]{8}[0-9])\b",
        text,
        re.I,
    )
    cusips = {re.sub(r"\s", "", value).upper() for value in identifiers_text}
    if (
        len(cusips) != 1
        or len(identifiers_text) != len(re.findall(r"\bCUSIP\b", text, re.I))
        or not valid_cusip(next(iter(cusips)))
    ):
        raise IdentityEvidenceError("missing_or_ambiguous_certificate_cusip")
    return {
        "issuer_cik": cik,
        "form": forms[0].decode(),
        "accession": accession,
        "reported_filing_date": filing_date.isoformat(),
        "reported_local_acceptance_datetime": accepted[0].decode(),
        "available_by": available.isoformat(),
        "availability_basis": "SEC_header_filing_date_plus_fourteen_calendar_days_date_only",
        "security_title": titles[0],
        "cusip": next(iter(cusips)),
        "filename": filename,
        "document_sha256": hashlib.sha256(texts[0]).hexdigest(),
        "complete_submission_sha256": hashlib.sha256(payload).hexdigest(),
        "SEC_header_sha256": hashlib.sha256(headers[0]).hexdigest(),
        "issuer_identity_basis": "explicit_SEC_FILER_and_named_certificate_face",
    }
