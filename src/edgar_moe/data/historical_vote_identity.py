"""Corroborate a listed ordinary share by matching an issuer vote to SEC fund reports."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .historical_identity import (
    MAX_DOCUMENT_BYTES,
    PUBLICATION_LAG_DAYS,
    IdentityEvidenceError,
    _soup,
    valid_cusip,
)
from .historical_listed_isin import _flat, _one, valid_isin


def _submission(
    raw: bytes, ref: dict[str, str], form: str, cutoff: datetime
) -> tuple[dict[str, str], str]:
    if not 0 < len(raw) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("vote_identity_submission_size")
    header = _one(
        re.findall(rb"<SEC-HEADER>(.*?)</SEC-HEADER>", raw, re.S), "vote_identity_header_count"
    )

    def field(label: bytes) -> str:
        values: list[bytes] = re.findall(rb"(?m)^[ \t]*" + label + rb":[ \t]*([^\r\n]+)", header)
        return _one(values, "vote_identity_header_field").decode("ascii").strip()

    if (
        field(b"ACCESSION NUMBER") != ref["accession"]
        or field(b"CONFORMED SUBMISSION TYPE") != form
        or field(b"CENTRAL INDEX KEY") != ref["filer_cik"]
    ):
        raise IdentityEvidenceError("vote_identity_filing_identity")
    raw_date = _one(
        re.findall(rb"(?m)^<ACCEPTANCE-DATETIME>([0-9]{14})[ \t\r]*$", header),
        "vote_identity_acceptance",
    )
    accepted_local = datetime.strptime(raw_date.decode(), "%Y%m%d%H%M%S").replace(
        tzinfo=ZoneInfo("America/New_York")
    )
    filed = datetime.strptime(field(b"FILED AS OF DATE"), "%Y%m%d").date()
    if not 0 <= (filed - accepted_local.date()).days <= 3:
        raise IdentityEvidenceError("vote_identity_filing_date_conflict")
    accepted = accepted_local.astimezone(UTC)
    available = max(
        accepted, datetime.combine(filed, time.min, ZoneInfo("America/New_York")).astimezone(UTC)
    ) + timedelta(days=PUBLICATION_LAG_DAYS)
    if available > cutoff or (cutoff - accepted).days > 730:
        raise IdentityEvidenceError("vote_identity_filing_unavailable")
    documents = re.findall(rb"<DOCUMENT>(.*?)</DOCUMENT>", raw, re.S)
    primary = _one(
        [
            doc
            for doc in documents
            if re.search(rb"(?m)^<TYPE>" + form.encode() + rb"[ \t\r]*$", doc)
        ],
        "vote_identity_primary_document",
    )
    body = _one(re.findall(rb"<TEXT>(.*?)</TEXT>", primary, re.S), "vote_identity_primary_text")
    return (
        {
            "accession": ref["accession"],
            "filer_cik": ref["filer_cik"],
            "available_by": available.isoformat(),
            "filing_date": filed.isoformat(),
            "document_sha256": hashlib.sha256(raw).hexdigest(),
        },
        _flat(_soup(body).get_text(" ", strip=True)),
    )


def legal_names(name: str) -> tuple[str, str]:
    """Accept only the exact expansion of a public limited company suffix."""
    match = re.fullmatch(r"(.+) (?:PLC|PUBLIC LIMITED COMPANY)", name, re.I)
    if match is None:
        raise IdentityEvidenceError("vote_identity_unqualified_registrant")
    return match[1] + " Public Limited Company", match[1] + " PLC"


def issuer_meeting(
    raw: bytes,
    reference: dict[str, str],
    periodic: dict[str, Any],
    *,
    issuer_name: str,
    cutoff: datetime,
) -> dict[str, str]:
    if reference["filer_cik"] != periodic["issuer_cik"]:
        raise IdentityEvidenceError("vote_identity_issuer_cik")
    evidence, body = _submission(raw, reference, "8-K", cutoff)
    long_name, _ = legal_names(issuer_name)
    registered = (
        re.escape(periodic["security_title"])
        + r"\s+"
        + re.escape(periodic["symbol_at_cutoff"])
        + r"\s+"
        + re.escape(periodic["exchange_at_filing"])
    )
    covers = re.findall(
        r"Securities registered pursuant to Section 12\(b\) of the Act:(.*?)(?:Indicate by check mark|Item [0-9]+\.[0-9]+)",
        body,
        re.I,
    )
    heading = r"Title of each class Trading Symbol\(s\) Name of each exchange on which registered "
    if len(covers) != 1 or re.fullmatch(heading + registered, covers[0].strip(), re.I) is None:
        raise IdentityEvidenceError("vote_identity_issuer_registration")
    dates = r"[A-Za-z]+ [0-9]{1,2}, [0-9]{4}"
    years = r"[0-9]{4}"
    meeting = _one(
        re.findall(
            rf"On ({dates}), {re.escape(long_name)} \(the “Company”\) held its ({years}) "
            r"Annual General Meeting of Shareholders",
            body,
            re.I,
        ),
        "vote_identity_issuer_meeting",
    )
    record = _one(
        re.findall(
            rf"\bordinary shares outstanding and entitled to vote as of ({dates}), the record date",
            body,
            re.I,
        ),
        "vote_identity_issuer_record_date",
    )
    meeting_date = datetime.strptime(meeting[0], "%B %d, %Y").date()
    record_date = datetime.strptime(record, "%B %d, %Y").date()
    filed_date = datetime.fromisoformat(evidence["filing_date"]).date()
    if (
        int(meeting[1]) != meeting_date.year
        or not 0 < (meeting_date - record_date).days <= 120
        or not 0 <= (filed_date - meeting_date).days <= 14
    ):
        raise IdentityEvidenceError("vote_identity_meeting_date_conflict")
    return evidence | {
        "meeting_date": meeting_date.isoformat(),
        "record_date": record_date.isoformat(),
        "symbol": periodic["symbol_at_cutoff"],
    }


def voter(
    raw: bytes,
    reference: dict[str, str],
    meeting: dict[str, str],
    *,
    issuer_name: str,
    cutoff: datetime,
    require_record_date: bool,
) -> dict[str, str]:
    evidence, body = _submission(raw, reference, "N-PX", cutoff)
    long_name, short_name = legal_names(issuer_name)
    ticker = re.escape(meeting["symbol"])
    isin = None
    if require_record_date:
        matches = re.findall(
            rf"\b{re.escape(long_name)} Ticker: {ticker} Security ID: ([A-Z0-9]{{9}}) "
            r"Meeting Date: ([A-Za-z]+ [0-9]{1,2}, [0-9]{4}) Meeting Type: Annual "
            r"Record Date: ([A-Za-z]+ [0-9]{1,2}, [0-9]{4})",
            body,
            re.I,
        )
        cusip, vote_date, record_date = _one(matches, "vote_identity_record_date_voter_match")
        matched_date = datetime.strptime(vote_date, "%b %d, %Y").date().isoformat()
        if datetime.strptime(record_date, "%b %d, %Y").date().isoformat() != meeting["record_date"]:
            raise IdentityEvidenceError("vote_identity_voter_record_date")
    else:
        matches = re.findall(
            rf"\b{re.escape(short_name)} Agenda Number: [0-9]+ -{{5,}} "
            rf"Security: ([A-Z0-9]{{9}}) Meeting Type: Annual "
            rf"Meeting Date: ([0-9]{{2}}-[A-Za-z]+-[0-9]{{4}}) Ticker: {ticker} ISIN: ([A-Z0-9]{{12}})\b",
            body,
            re.I,
        )
        cusip, vote_date, isin = _one(matches, "vote_identity_voter_match")
        if not valid_isin(isin):
            raise IdentityEvidenceError("vote_identity_voter_isin")
        matched_date = datetime.strptime(vote_date, "%d-%b-%Y").date().isoformat()
    if (
        matched_date != meeting["meeting_date"]
        or evidence["filing_date"] < matched_date
        or not valid_cusip(cusip)
    ):
        raise IdentityEvidenceError("vote_identity_voter_security_or_date")
    return (
        evidence | {"cusip": cusip, "meeting_date": matched_date} | ({"isin": isin} if isin else {})
    )
