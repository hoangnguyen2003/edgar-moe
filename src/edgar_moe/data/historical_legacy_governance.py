"""A narrow, offline pre-2000 SGML charter proof for one common-stock class.

Preferred stock remains a different class. Only an explicit common voting
qualifier is supplied; no identifier, equity-family alias or market continuity
is inferred. A modern submission cannot fall back to this legacy format.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, time, timedelta
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from edgar_moe.data.historical_governance import _par_value
from edgar_moe.data.historical_identity import (
    MAX_DOCUMENT_BYTES,
    MAX_RETAINED_DOCUMENT_BYTES,
    IdentityEvidenceError,
    class_signature,
)


def name_key(value: str) -> str:
    return "".join(c for c in value.casefold() if c.isalnum())


def _date(value: str) -> str:
    try:
        return datetime.strptime(value, "%B %d, %Y").date().isoformat()
    except ValueError as error:
        raise IdentityEvidenceError("invalid_legacy_governing_reference_date") from error


def legacy_reference(payload: bytes, *, cik: str) -> dict[str, str]:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_RETAINED_DOCUMENT_BYTES:
        raise IdentityEvidenceError("invalid_legacy_governing_index_bytes")
    if not re.fullmatch(r"[0-9]{10}", cik) or int(cik) == 0:
        raise IdentityEvidenceError("invalid_legacy_governing_issuer")
    rows = []
    for row in BeautifulSoup(payload, "html.parser").find_all("tr"):
        if row.find("tr") is not None:
            continue
        text = " ".join(row.get_text(" ", strip=True).split())
        if re.search(r"(?:certificate|articles) of (?:incorporation|amendment)", text, re.I):
            rows.append((row, text))
    if len(rows) != 1:
        raise IdentityEvidenceError("ambiguous_legacy_governing_references")
    row, text = rows[0]
    date = r"[A-Za-z]+ [0-9]{1,2}, [0-9]{4}"
    match = re.fullmatch(
        rf"3\.1 Amended and Restated Certificate of Incorporation dated ({date}) - "
        r"incorporated by reference to Exhibit 3\.1 to the Company[’']s Registration "
        rf"Statement No\. ([0-9]+-[0-9]+) on Form S-4 filed with the Commission on ({date})\.?",
        text,
    )
    links = {str(a["href"]) for a in row.find_all("a", href=True)}
    if match is None or len(links) != 1:
        raise IdentityEvidenceError("unsupported_legacy_governing_reference")
    parsed = urlsplit(next(iter(links)))
    accession = re.fullmatch(
        rf"/Archives/edgar/data/{int(cik)}/([0-9]{{10}}-[0-9]{{2}}-[0-9]{{6}})\.txt",
        parsed.path,
    )
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc != "www.sec.gov"
        or parsed.query
        or parsed.fragment
        or accession is None
    ):
        raise IdentityEvidenceError("unbound_legacy_governing_link")
    adopted, filed = _date(match[1]), _date(match[3])
    if not adopted <= filed < "2000-01-01":
        raise IdentityEvidenceError("nonlegacy_governing_reference_date")
    return {
        "issuer_cik": cik,
        "accession": accession[1],
        "url": "https://www.sec.gov" + parsed.path,
        "referenced_exhibit": "3.1",
        "referenced_form": "S-4",
        "referenced_file_number": match[2],
        "referenced_filing_date": filed,
        "restatement_date": adopted,
        "index_description_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def _words(value: str) -> int:
    digits = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
    }
    scales = {"thousand": 1000, "million": 1000000, "billion": 1000000000}
    total = group = 0
    prior_scale = 10**12
    for word in value.casefold().split():
        if word in digits and group % 100 == 0:
            group += digits[word]
        elif word == "hundred" and 1 <= group <= 9:
            group *= 100
        elif word in scales and group > 0 and scales[word] < prior_scale:
            prior_scale = scales[word]
            total += group * prior_scale
            group = 0
        else:
            raise IdentityEvidenceError("unsupported_legacy_share_count_words")
    return total + group


def legacy_voting_evidence(
    payload: bytes, *, reference: dict[str, str], expected_title: str, cutoff: datetime
) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("invalid_legacy_governing_source_bytes")
    if class_signature(expected_title) != ("common", "", "voting"):
        raise IdentityEvidenceError("unsupported_legacy_registered_class")
    if (
        len(re.findall(rb"<SEC-DOCUMENT>", payload, re.I)) != 1
        or len(re.findall(rb"</SEC-DOCUMENT>", payload, re.I)) != 1
    ):
        raise IdentityEvidenceError("incomplete_legacy_submission")
    headers = re.findall(rb"<SEC-HEADER>(.*?)</SEC-HEADER>", payload, re.S | re.I)
    if len(headers) != 1 or len(headers[0]) > 256 * 1024:
        raise IdentityEvidenceError("ambiguous_legacy_governing_header")
    header = headers[0].replace(b"\r\n", b"\n")
    for field, expected in (
        ("ACCESSION NUMBER", reference["accession"]),
        ("CONFORMED SUBMISSION TYPE", "S-4"),
    ):
        if re.findall(rb"(?m)^" + field.encode() + rb":[ \t]*([^\n]+)", header) != [
            expected.encode()
        ]:
            raise IdentityEvidenceError("legacy_governing_header_identity_mismatch")
    filers = re.findall(rb"(?ms)^FILER:\s*\n(.*?)(?=^\S|\Z)", header)
    if len(filers) != 1 or b"<ACCEPTANCE-DATETIME>" in payload.upper():
        raise IdentityEvidenceError("unsupported_legacy_governing_header")
    ids = re.findall(rb"CENTRAL INDEX KEY:[ \t]*([0-9]+)", filers[0])
    names = re.findall(rb"COMPANY CONFORMED NAME:[ \t]*([^\n]+)", filers[0])
    numbers = re.findall(rb"SEC FILE NUMBER:[ \t]*([^\n]+)", filers[0])
    if (
        len(ids) != 1
        or ids[0].decode().zfill(10) != reference["issuer_cik"]
        or len(names) != 1
        or numbers != [reference["referenced_file_number"].encode()]
    ):
        raise IdentityEvidenceError("legacy_governing_filer_mismatch")
    dates = re.findall(rb"(?m)^FILED AS OF DATE:[ \t]*([0-9]{8})[ \t]*$", header)
    try:
        if len(dates) != 1:
            raise ValueError()
        filed = datetime.strptime(dates[0].decode(), "%Y%m%d").date()
        name = names[0].decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise IdentityEvidenceError("invalid_legacy_governing_date_or_name") from error
    available = datetime.combine(filed + timedelta(days=14), time.min, UTC)
    if (
        filed.isoformat() != reference["referenced_filing_date"]
        or filed.year >= 2000
        or cutoff.utcoffset() is None
        or available > cutoff
    ):
        raise IdentityEvidenceError("legacy_governing_date_mismatch")
    documents = re.findall(rb"<DOCUMENT>(.*?)</DOCUMENT>", payload, re.S | re.I)
    counts = re.findall(rb"(?m)^PUBLIC DOCUMENT COUNT:[ \t]*([0-9]+)[ \t]*$", header)
    if (
        not 2 <= len(documents) <= 128
        or counts != [str(len(documents)).encode()]
        or re.search(rb"<FILENAME>", payload, re.I)
    ):
        raise IdentityEvidenceError("unsupported_legacy_document_inventory")
    chosen = []
    for ordinal, doc in enumerate(documents, 1):
        if re.findall(rb"(?im)^<SEQUENCE>[ \t]*([^\r\n]+)", doc) != [str(ordinal).encode()]:
            raise IdentityEvidenceError("ambiguous_legacy_document_sequence")
        types = re.findall(rb"(?im)^<TYPE>[ \t]*([^\r\n]+)", doc)
        if len(types) != 1:
            raise IdentityEvidenceError("ambiguous_legacy_document_type")
        if ordinal == 1 and types[0] != b"S-4":
            raise IdentityEvidenceError("missing_legacy_primary_registration")
        if types[0].upper().startswith(b"EX-3"):
            if types[0] != b"EX-3.1":
                raise IdentityEvidenceError("additional_legacy_governing_exhibit")
            chosen.append(doc)
    if len(chosen) != 1:
        raise IdentityEvidenceError("ambiguous_legacy_governing_exhibit")
    texts = re.findall(rb"<TEXT>(.*?)</TEXT>", chosen[0], re.S | re.I)
    if len(texts) != 1:
        raise IdentityEvidenceError("ambiguous_legacy_governing_text")
    text = " ".join(BeautifulSoup(texts[0], "html.parser").get_text(" ", strip=True).split())
    legal = re.findall(r"FIRST: The name of the corporation is (.+?) \(hereinafter", text)
    articles = re.findall(r"FOURTH: (.*?) FIFTH:", text)
    adopted = re.findall(r"this ([0-9]{1,2})(?:st|nd|rd|th) day of ([A-Za-z]+), ([0-9]{4})\.", text)
    if (
        len(adopted) != 1
        or _date(f"{adopted[0][1]} {adopted[0][0]}, {adopted[0][2]}")
        != reference["restatement_date"]
    ):
        raise IdentityEvidenceError("legacy_restatement_date_mismatch")
    if (
        len(legal) != 1
        or not name_key(name)
        or name_key(legal[0]) != name_key(name)
        or len(articles) != 1
        or len(re.findall(r"\bFOURTH:", text)) != 1
        or re.search(r"\bclass(?:es)?\s+(?!of\b)[A-Z0-9]+\b|\bnon[ -]?voting\b", text, re.I)
        or len(re.findall(r"\bauthority to issue\b", text, re.I)) != 1
        or re.search(
            r"\bordinary (?:stock|shares)\b|\bcapital (?:stock|shares)\b|\bdepositary\b", text, re.I
        )
    ):
        raise IdentityEvidenceError("ambiguous_legacy_authorized_classes")
    article = articles[0]
    outside = text.replace(article, "")
    preamble = re.findall(
        r"increasing the number of authorized shares of Common Stock of the Corporation to ([0-9,]+) shares",
        outside,
    )
    if (
        len(preamble) != 1
        or len(re.findall(r"\bcommon (?:stock|shares)\b", outside, re.I)) != 1
        or re.search(r"\bpreferred\b", outside, re.I)
    ):
        raise IdentityEvidenceError("legacy_common_rights_outside_authorized_article")
    authorized = re.match(
        r"The total number of shares which the Corporation shall have authority to issue is "
        r"([A-Za-z ]+) \(([0-9,]+)\), consisting of ([A-Za-z ]+) \(([0-9,]+)\) shares of "
        r'Preferred Stock, of the par value of One Dollar \(\$1\.00\) per share \(hereinafter called "Preferred Stock"\), '
        r"and ([A-Za-z ]+) \(([0-9,]+)\) shares of Common Stock, of the par value of Ten Cents \(\$\.10\) per share "
        r'\(hereinafter called "Common Stock"\)\. ',
        article,
    )
    if authorized is None or _par_value(expected_title) != _par_value("$.10"):
        raise IdentityEvidenceError("unsupported_legacy_share_authorization")
    numbers = [authorized[i] for i in (2, 4, 6)] + [preamble[0]]
    if any(not re.fullmatch(r"(?:[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*)", n) for n in numbers):
        raise IdentityEvidenceError("invalid_legacy_share_count_format")
    counts_int = [int(value.replace(",", "")) for value in numbers[:3]]
    if (
        any(_words(authorized[i]) != count for i, count in zip((1, 3, 5), counts_int, strict=True))
        or counts_int[0] != counts_int[1] + counts_int[2]
        or int(preamble[0].replace(",", "")) != counts_int[2]
    ):
        raise IdentityEvidenceError("legacy_authorized_share_count_conflict")
    rights = article[authorized.end() :]
    voting = re.search(r"Except as otherwise expressly provided .*", rights)
    if (
        voting is None
        or len(re.findall(r"\bExcept as otherwise expressly provided\b", rights)) != 1
    ):
        raise IdentityEvidenceError("missing_legacy_common_voting_rights")
    # This complete sentence permits preferred-series rights, while explicitly
    # giving each common share one vote. It cannot qualify preferred stock.
    expected = (
        "Except as otherwise expressly provided in any resolution adopted by the Board of Directors "
        "granting voting rights to the holders of shares of any series of Preferred Stock and except "
        "as otherwise required by law the entire voting power of the Corporation shall be vested in "
        "the Common Stock, and each share of Common Stock shall have one vote for each share thereof held."
    )
    nonvoting = rights[: voting.start()]
    preferred, *economic = re.split(
        r"(?=Subject to the payment|In the event of the liquidation)", nonvoting
    )
    if (
        voting[0] != expected
        or len(economic) != 2
        or re.search(r"\bcommon (?:stock|shares)\b", preferred, re.I)
        or not preferred.startswith(
            "The Board of Directors is hereby authorized to issue the shares of the Preferred Stock in one or more series,"
        )
        or re.search(r"\bvot(?:e|es|ing)\b", " ".join(economic), re.I)
    ):
        raise IdentityEvidenceError("unsupported_legacy_common_rights")
    return {
        "issuer_cik": reference["issuer_cik"],
        "accession": reference["accession"],
        "referenced_exhibit": "3.1",
        "reported_filing_date": filed.isoformat(),
        "available_by": available.isoformat(),
        "availability_basis": "SEC_header_filing_date_plus_fourteen_days_legacy_no_acceptance_timestamp",
        "security_title": expected_title,
        "reported_par_value": "$.10",
        "votes_per_share": 1,
        "authorized_common_shares": counts_int[2],
        "authorized_preferred_shares": counts_int[1],
        "issuer_name_key_sha256": hashlib.sha256(name_key(name).encode()).hexdigest(),
        "complete_submission_sha256": hashlib.sha256(payload).hexdigest(),
        "SEC_header_sha256": hashlib.sha256(headers[0]).hexdigest(),
        "document_sha256": hashlib.sha256(texts[0]).hexdigest(),
        "authorized_article_sha256": hashlib.sha256(article.encode()).hexdigest(),
    }
