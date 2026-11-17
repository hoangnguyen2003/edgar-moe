"""An issuer's explicit common/capital definition and exact filed ownership citation.

This is a dated, source-qualified correspondence, never a generic stock-family
alias or evidence of continuity in market-provider identifiers.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from edgar_moe.data.historical_class_charter import _name as legal_name_key
from edgar_moe.data.historical_governance import _par_value
from edgar_moe.data.historical_identity import (
    IdentityEvidenceError,
    _ownership_subject_name,
    class_signature,
    instant,
    ownership_identity,
)
from edgar_moe.data.historical_proxy_class import _submission

NUMBER = r"(?:0|[1-9][0-9]*)(?:,[0-9]{3})*"
DATE = r"[A-Za-z]+ [0-9]{1,2}, [0-9]{4}"


def _text(raw: bytes | str) -> str:
    return " ".join(
        BeautifulSoup(raw, "html.parser").get_text(" ", strip=True).replace("\u200b", "").split()
    )


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _address(value: str) -> str:
    # Postal suffix expansion only, never an issuer/reporter-name alias.
    value = re.sub(r"\bBlvd\.?\b", "Boulevard", value, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", value.lower())


def qualify_capital_definition(
    periodic: dict[str, Any],
    raw: bytes,
    row: dict[str, Any],
    proxy_raw: bytes,
    *,
    proxy_accession: str,
    proxy_filename: str,
    cutoff: datetime,
) -> dict[str, Any]:
    cik = periodic["issuer_cik"]
    subject_name, subject = _ownership_subject_name(raw, row, cik)
    own = ownership_identity(raw, row, cik=cik, cutoff=cutoff)
    if (
        class_signature(periodic["security_title"]) != ("capital", "", "")
        or class_signature(own["security_title"]) != ("common", "", "")
        or (
            "$" in own["security_title"]
            and _par_value(own["security_title"]) != _par_value(periodic["security_title"])
        )
    ):
        raise IdentityEvidenceError("capital_definition_cannot_override_class_voting_or_par")
    header, primary, proxy_cik, issuer, dates = _submission(
        proxy_raw,
        accession=proxy_accession,
        form="DEF 14A",
        filename=proxy_filename,
        role="FILER",
        cutoff=cutoff,
    )
    if proxy_cik != cik or legal_name_key(issuer) != legal_name_key(subject_name.decode()):
        raise IdentityEvidenceError("capital_definition_proxy_subject_mismatch")
    own_header, own_primary, reporter_cik, reporter, own_dates = _submission(
        raw,
        accession=own["accession"],
        form="SC 13G/A",
        filename=row["primaryDocument"],
        role="FILED BY",
        cutoff=cutoff,
    )
    if (
        instant(own["available_by"]) > instant(dates["available_by"])
        or own_dates["filed_date"] > dates["filed_date"]
    ):
        raise IdentityEvidenceError("capital_definition_cites_unavailable_ownership")
    text = _text(primary)
    # Require a literal registrant cover, separate from proposal/quote content.
    covers = re.findall(
        r"([A-Za-z][A-Za-z .,'&()-]{2,120}) \(Name of Registrant as Specified In Its Charter\)",
        text,
    )
    if len(covers) != 1 or legal_name_key(covers[0]) != legal_name_key(issuer):
        raise IdentityEvidenceError("capital_definition_named_proxy_registrant_missing")
    definitions = list(
        re.finditer(
            r"Which ([A-Za-z][A-Za-z .&-]{0,60}) shares will be entitled to vote at the Annual Meeting\? "
            r"\1[’']s common stock \((\$[0-9]+(?:\.[0-9]+)? par value capital stock)\) "
            r"is the only class of security entitled to vote at the Annual Meeting\.",
            text,
        )
    )
    if len(definitions) != 1 or _par_value(definitions[0][2]) != _par_value(
        periodic["security_title"]
    ):
        raise IdentityEvidenceError("missing_or_conflicting_explicit_capital_definition")
    # Qualification binds the singular definition to its immediately following
    # one-vote rule, rather than selecting an isolated common-stock mention.
    suffix = text[definitions[0].end() : definitions[0].end() + 500]
    if not re.match(
        r" Each record owner and each stockholder who holds stock in street name at the close of business "
        r"as of the record date is entitled to one vote for each share held at the meeting, "
        r"or any adjournment or postponement\.",
        suffix,
    ):
        raise IdentityEvidenceError("capital_definition_voting_rule_missing")
    own_text = _text(own_primary)
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
        raise IdentityEvidenceError("capital_definition_reporter_identity_mismatch")
    display = names[0][0]
    addresses = re.findall(
        r"Item 2\(b\) [–-] Address of Principal Business Office or, if none, residence: ([^\n]{1,180}?) Item 2\(c\)",
        own_text,
        re.I,
    )
    events = re.findall(
        rf"Date of Event Which Requires Filing of this Statement: ({DATE})", own_text, re.I
    )
    powers = re.findall(
        rf"5\. SOLE VOTING POWER ({NUMBER}) 6\. SHARED VOTING POWER ({NUMBER}) "
        rf"7\. SOLE DISPOSITIVE POWER ({NUMBER}) 8\. SHARED DISPOSITIVE POWER ({NUMBER}) "
        rf"9\. AGGREGATE AMOUNT BENEFICIALLY OWNED BY EACH REPORTING PERSON ({NUMBER}) 10\.",
        own_text,
        re.I,
    )
    if len(addresses) != 1 or len(events) != 1 or len(powers) != 1:
        raise IdentityEvidenceError("ambiguous_capital_definition_ownership_fields")
    counts = tuple(int(v.replace(",", "")) for v in powers[0])
    if not (
        counts[0] == 0
        and 0 < counts[1] <= counts[4] < 10**12
        and counts[2] > 0
        and counts[3] > 0
        and counts[2] + counts[3] == counts[4]
    ):
        raise IdentityEvidenceError("unsupported_capital_definition_positions")
    # The same named short issuer reference must label its ownership table.
    short = re.escape(definitions[0][1])
    headings = list(
        re.finditer(
            rf"Security Ownership of Certain Beneficial Owners The following sets forth information as to any person known to {short} "
            rf"to be the beneficial owner of more than five percent of {short}[’']s common stock as of ({DATE})\.",
            text,
        )
    )
    if len(headings) != 1:
        raise IdentityEvidenceError("capital_definition_ownership_heading_missing")
    tables = []
    soup = BeautifulSoup(primary, "html.parser")
    for table in soup.find_all("table"):
        if table.find("table") is not None:
            continue
        cells_text = _text(str(table))
        if not cells_text.startswith(
            "Name and address Number of Shares Beneficially Owned Percent of Class "
        ):
            continue
        for tr in table.find_all("tr"):
            cells = [_text(str(c)) for c in tr.find_all(["td", "th"], recursive=False)]
            cells = [c for c in cells if c]
            if len(cells) != 3:
                continue
            match = re.fullmatch(rf"{re.escape(display)} \(([1-9][0-9]?)\) (.+)", cells[0])
            if (
                match
                and re.fullmatch(NUMBER, cells[1])
                and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?%", cells[2])
            ):
                tables.append(
                    (
                        match[1],
                        match[2],
                        int(cells[1].replace(",", "")),
                        str(tr),
                        cells_text,
                        cells[2],
                    )
                )
    if (
        len(tables) != 1
        or tables[0][2] != counts[4]
        or _address(tables[0][1]) != _address(addresses[0])
    ):
        raise IdentityEvidenceError("capital_definition_reporter_table_mismatch")
    table_start = text.find(tables[0][4], headings[0].end())
    if (
        table_start < 0
        or text[headings[0].end() : table_start].strip()
        or not 0 < float(tables[0][5][:-1]) <= 100
    ):
        raise IdentityEvidenceError("capital_definition_table_unbound_to_class_heading")
    marker = re.escape(tables[0][0])
    cites = list(
        re.finditer(
            rf"\({marker}\) Based on the Schedule 13G filed with the Securities and Exchange Commission on ({DATE}) "
            rf"by {re.escape(display)} and certain subsidiaries \(([A-Za-z][A-Za-z ]{{0,50}})\)\. "
            rf"\2 reported that it does not have sole voting power over any shares, and has shared voting power over ({NUMBER}) shares, "
            rf"sole dispositive power over ({NUMBER}) shares, and shared dispositive power over ({NUMBER}) shares\.",
            text,
        )
    )
    if len(cites) != 1:
        raise IdentityEvidenceError("capital_definition_exact_citation_missing")
    cite = cites[0]
    table_end = table_start + len(tables[0][4])
    if not table_end <= cite.start() <= table_end + 20000:
        raise IdentityEvidenceError("capital_definition_citation_outside_ownership_section")
    try:
        filed, event, as_of = [
            datetime.strptime(v, "%B %d, %Y").date().isoformat()
            for v in (cite[1], events[0], headings[0][1])
        ]
    except ValueError as error:
        raise IdentityEvidenceError("capital_definition_citation_date_invalid") from error
    if (
        filed != own_dates["filed_date"]
        or not event == as_of <= filed
        or tuple(int(cite[i].replace(",", "")) for i in (3, 4, 5)) != counts[1:4]
    ):
        raise IdentityEvidenceError("capital_definition_citation_date_or_positions_mismatch")
    return {
        **own,
        "security_title": periodic["security_title"],
        "available_by": max(
            instant(own["available_by"]), instant(dates["available_by"])
        ).isoformat(),
        "availability_basis": "maximum_ownership_and_explicit_issuer_capital_definition",
        "issuer_capital_definition_evidence": {
            "basis": "explicit_issuer_common_capital_par_voting_definition_and_exact_reporter_citation",
            "reported_ownership_title": own["security_title"],
            "reported_ownership_available_by": own["available_by"],
            "proxy_accession": proxy_accession,
            "proxy_primary_filename": proxy_filename,
            **dates,
            "complete_proxy_submission_sha256": _hash(proxy_raw),
            "proxy_SEC_header_sha256": _hash(header),
            "proxy_primary_sha256": _hash(primary),
            "definition_sha256": _hash(definitions[0][0].encode()),
            "citation_sha256": _hash(cite[0].encode()),
            "ownership_table_row_sha256": _hash(tables[0][3].encode()),
            "reporter_cik": reporter_cik,
            "reporter_legal_name_sha256": _hash(reporter.encode()),
            "reporter_cover_name_sha256": _hash(display.encode()),
            "ownership_subject_evidence": subject,
            "ownership_SEC_header_sha256": _hash(own_header),
            "ownership_event_date": event,
            "proxy_reported_ownership_filing_date": filed,
            "address_comparison": "alphanumeric_casefold_with_postal_BLVD_expansion_only",
        },
    }
