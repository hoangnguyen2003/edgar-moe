"""Qualify a missing common-stock class from an issuer's explicit proxy definition.

The supported shared-voting citation must match a subject-authenticated 13G/A,
including its reporter identity, address, dates and complete share positions.
This is source attribution only, not historical market-continuity evidence.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from edgar_moe.data.historical_class_charter import _name as legal_name_key
from edgar_moe.data.historical_identity import (
    IdentityEvidenceError,
    _ownership_subject_name,
    class_signature,
    instant,
    ownership_identity,
)
from edgar_moe.data.historical_proxy_class import _submission

_NUMBER = r"(?:0|[1-9][0-9]*)(?:,[0-9]{3})*"
_DATE = r"[A-Za-z]+ [0-9]{1,2}, [0-9]{4}"


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _proxy_text(primary: bytes) -> tuple[str, dict[str, Any]]:
    soup = BeautifulSoup(primary, "html.parser")
    footers = []
    headings = []
    for page in soup.find_all("div"):
        style = re.sub(r"\s", "", str(page.get("style", ""))).lower()
        if "page-break-after:always;" not in style:
            continue
        children = page.find_all("div", recursive=False)
        if len(children) < 3:
            continue
        first, last = children[0], children[-1]
        links = first.find_all("a")
        if (
            first.get_text(" ", strip=True) == "TABLE OF CONTENTS"
            and len(links) == 1
            and links[0].get("href") == "#TOC"
            and links[0].get_text(" ", strip=True) == "TABLE OF CONTENTS"
        ):
            headings.append(first)
        number = last.get_text(" ", strip=True)
        centered = last.find_all("div", recursive=False)
        if (
            re.fullmatch(r"[1-9][0-9]{0,2}", number)
            and len(centered) == 1
            and "text-align:center;" in re.sub(r"\s", "", str(centered[0].get("style", "")))
            and centered[0].get_text(" ", strip=True) == number
            and last.find("table") is None
        ):
            footers.append((int(number), last))
    # Only a complete consecutive folio sequence can be removed. Business
    # counts and arbitrary narrative text are never stripped by a text regex.
    numbers = [number for number, _ in footers]
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        raise IdentityEvidenceError("proxy_definition_nonconsecutive_page_footers")
    removed = []
    for node in [*headings, *(node for _, node in footers)]:
        removed.append(_hash(str(node).encode()))
        node.decompose()
    text = " ".join(soup.get_text(" ", strip=True).replace("\u200b", "").split())
    return text, {"removed_page_furniture_sha256": removed, "numbered_pages": len(numbers)}


def qualify_proxy_definition(
    periodic: dict[str, Any],
    ownership_raw: bytes,
    ownership_metadata: dict[str, Any],
    proxy_raw: bytes,
    *,
    proxy_accession: str,
    proxy_filename: str,
    cutoff: datetime,
) -> dict[str, Any]:
    cik = periodic["issuer_cik"]
    subject_name, subject = _ownership_subject_name(ownership_raw, ownership_metadata, cik)
    own = ownership_identity(ownership_raw, ownership_metadata, cik=cik, cutoff=cutoff)
    registered = class_signature(periodic["security_title"])
    if (
        registered[0] != "common"
        or not registered[1]
        or registered[2]
        or class_signature(own["security_title"]) != ("common", "", "")
    ):
        raise IdentityEvidenceError("proxy_definition_cannot_override_family_class_or_voting")
    header, primary, proxy_cik, issuer, dates = _submission(
        proxy_raw,
        accession=proxy_accession,
        form="DEF 14A",
        filename=proxy_filename,
        role="FILER",
        cutoff=cutoff,
    )
    if proxy_cik != cik or legal_name_key(issuer) != legal_name_key(subject_name.decode("utf-8")):
        raise IdentityEvidenceError("proxy_definition_subject_issuer_mismatch")
    own_header, own_primary, reporter_cik, reporter, own_dates = _submission(
        ownership_raw,
        accession=own["accession"],
        form="SC 13G/A",
        filename=ownership_metadata["primaryDocument"],
        role="FILED BY",
        cutoff=cutoff,
    )
    if own_dates["filed_date"] > dates["filed_date"] or instant(own["available_by"]) > instant(
        dates["available_by"]
    ):
        raise IdentityEvidenceError("proxy_definition_cites_unavailable_ownership")
    text, furniture = _proxy_text(primary)
    definitions = re.findall(
        r'We refer to our (Class [A-Z0-9]+ common stock) as our [“"]common stock\.[”"]|'
        r'We refer to our (Class [A-Z0-9]+ common stock) as our [“"]common stock[”"]\.',
        text,
        re.I,
    )
    if len(definitions) != 1 or class_signature(next(v for v in definitions[0] if v)) != registered:
        raise IdentityEvidenceError("missing_or_conflicting_proxy_common_class_definition")
    own_text = " ".join(BeautifulSoup(own_primary, "html.parser").get_text(" ", strip=True).split())
    names = re.findall(
        r"1\. NAME OF REPORTING PERSON I\.R\.S\. IDENTIFICATION NO\. OF ABOVE PERSON "
        r"([^\n]{1,120}?) - ([0-9]{2}-[0-9]{7}) 2\.",
        own_text,
        re.I,
    )
    roles = re.findall(rb"(?ms)^FILED BY:\s*\n(.*?)(?=^\S|\Z)", own_header)
    irs = (
        re.findall(rb"IRS NUMBER:[ \t]*([0-9]{9})[ \t]*$", roles[0], re.M)
        if len(roles) == 1
        else []
    )
    if (
        len(names) != 1
        or len(irs) != 1
        or irs[0] == b"000000000"
        or names[0][1].replace("-", "") != irs[0].decode()
    ):
        raise IdentityEvidenceError("proxy_definition_reporter_cover_header_identity_mismatch")
    display_name = names[0][0]
    addresses = re.findall(
        r"Item 2\(b\) [–-] Address of Principal Business Office or, if none, residence: ([^\n]{1,180}?) Item 2\(c\)",
        own_text,
        re.I,
    )
    events = re.findall(
        rf"Date of Event Which Requires Filing of this Statement: ({_DATE})", own_text, re.I
    )
    powers = re.findall(
        rf"5\. SOLE VOTING POWER ({_NUMBER}) 6\. SHARED VOTING POWER ({_NUMBER}) "
        rf"7\. SOLE DISPOSITIVE POWER ({_NUMBER}) 8\. SHARED DISPOSITIVE POWER ({_NUMBER}) "
        rf"9\. AGGREGATE AMOUNT BENEFICIALLY OWNED BY EACH REPORTING PERSON ({_NUMBER}) 10\.",
        own_text,
        re.I,
    )
    if len(addresses) != 1 or len(events) != 1 or len(powers) != 1:
        raise IdentityEvidenceError("ambiguous_proxy_definition_ownership_cover")
    counts = tuple(int(v.replace(",", "")) for v in powers[0])
    if not (
        counts[0] == 0
        and 0 < counts[1] <= counts[4] < 10**12
        and counts[2] > 0
        and counts[3] > 0
        and counts[2] + counts[3] == counts[4]
    ):
        raise IdentityEvidenceError("unsupported_proxy_definition_ownership_positions")
    pattern = (
        rf"Based on a Schedule 13G/A filed with the SEC by {re.escape(display_name)}, "
        rf"([^\n]{{1,180}}?), on ({_DATE}), reflecting beneficial ownership as of ({_DATE}), "
        rf"{re.escape(display_name)} has shared voting power as to ({_NUMBER}) shares of common stock, "
        rf"sole dispositive power as to ({_NUMBER}) shares of common stock and shared dispositive power "
        rf"as to ({_NUMBER}) shares of common stock\."
    )
    citations = list(re.finditer(pattern, text, re.I))
    if len(citations) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_proxy_definition_citation")
    match = citations[0]
    try:
        filed_date, event_date, cover_event = [
            datetime.strptime(v, "%B %d, %Y").date().isoformat()
            for v in (match[2], match[3], events[0])
        ]
    except ValueError as error:
        raise IdentityEvidenceError("invalid_proxy_definition_citation_dates") from error
    cited = tuple(int(match[i].replace(",", "")) for i in (4, 5, 6))
    if (
        _key(match[1]) != _key(addresses[0])
        or filed_date != own_dates["filed_date"]
        or event_date != cover_event
        or event_date > filed_date
        or cited != (counts[1], counts[2], counts[3])
    ):
        raise IdentityEvidenceError("proxy_definition_citation_address_date_or_positions_mismatch")
    return {
        **own,
        "security_title": f"Class {registered[1]} " + own["security_title"],
        "available_by": max(
            instant(own["available_by"]), instant(dates["available_by"])
        ).isoformat(),
        "availability_basis": "maximum_of_ownership_and_issuer_proxy_definition_evidence",
        "issuer_proxy_definition_evidence": {
            "basis": "issuer_defined_common_class_and_filed_reporter_IRS_address_dates_shared_positions",
            "reported_ownership_title": own["security_title"],
            "reported_ownership_available_by": own["available_by"],
            "proxy_accession": proxy_accession,
            "proxy_primary_filename": proxy_filename,
            **dates,
            "complete_proxy_submission_sha256": _hash(proxy_raw),
            "proxy_SEC_header_sha256": _hash(header),
            "proxy_primary_sha256": _hash(primary),
            "citation_sha256": _hash(match[0].encode()),
            "reporter_cik": reporter_cik,
            "reporter_legal_name_sha256": _hash(reporter.encode()),
            "reporter_cover_name_sha256": _hash(display_name.encode()),
            "reporter_SEC_header_sha256": _hash(own_header),
            "ownership_subject_evidence": subject,
            "proxy_reported_ownership_filing_date": filed_date,
            "proxy_reported_ownership_event_date": event_date,
            **furniture,
        },
    }
