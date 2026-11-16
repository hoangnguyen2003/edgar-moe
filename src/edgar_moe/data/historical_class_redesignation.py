"""Dated issuer-charter evidence for a literal one-for-one common-class rename.

This is an explicit source-qualified redesignation, never a generic rule for
dropping class letters, merging voting rights or equating equity families.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from edgar_moe.data.historical_certificate import certificate_location
from edgar_moe.data.historical_class_charter import _name as legal_name_key
from edgar_moe.data.historical_governance import _par_value
from edgar_moe.data.historical_identity import (
    MAX_RETAINED_DOCUMENT_BYTES,
    IdentityEvidenceError,
    class_signature,
    instant,
)
from edgar_moe.data.historical_proxy_class import _submission

_NUMBER = r"[1-9][0-9]*(?:,[0-9]{3})*"
_DATE = r"[A-Za-z]+ [0-9]{1,2}, [0-9]{4}"
_PAR = r"\$[0-9]*(?:\.[0-9]+)?"
_QUOTE = r'[“"]\s*'
_CLOSE = r'\s*[”"]'


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _text(raw: bytes) -> str:
    return " ".join(BeautifulSoup(raw, "html.parser").get_text(" ", strip=True).split())


def redesignation_reference(payload: bytes, *, parent_url: str, cik: str) -> dict[str, str]:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_RETAINED_DOCUMENT_BYTES:
        raise IdentityEvidenceError("invalid_redesignation_index_bytes")
    rows = []
    for row in BeautifulSoup(payload, "html.parser").find_all("tr"):
        if row.find("tr") is not None:
            continue
        text = " ".join(row.get_text(" ", strip=True).split())
        if re.match(r"3\.[0-9]+\b", text) and re.search(
            r"Certificate of Incorporation", text, re.I
        ):
            rows.append((row, text))
    if len(rows) != 1:
        raise IdentityEvidenceError("ambiguous_redesignation_current_charter_reference")
    row, text = rows[0]
    match = re.fullmatch(
        r"(3\.[0-9]+) Third Amended and Restated Certificate of Incorporation "
        r"10-Q ([0-9]{3}-[0-9]{5}) (3\.[0-9]+) ([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
        text,
        re.I,
    )
    links = row.find_all("a", href=True)
    if match is None or match[1] != match[3] or len(links) != 1:
        raise IdentityEvidenceError("unsupported_redesignation_charter_reference")
    try:
        filed = datetime.strptime(match[4], "%m/%d/%Y").date().isoformat()
    except ValueError as error:
        raise IdentityEvidenceError("invalid_redesignation_reference_date") from error
    return {
        **certificate_location(str(links[0]["href"]), parent_url, cik),
        "exhibit": match[3],
        "sec_file_number": match[2],
        "referenced_filing_date": filed,
        "index_description_sha256": _hash(text.encode()),
    }


def redesignation_evidence(
    raw: bytes,
    *,
    reference: dict[str, str],
    primary_filename: str,
    cik: str,
    expected_title: str,
    cutoff: datetime,
) -> dict[str, Any]:
    if class_signature(expected_title) != ("common", "", ""):
        raise IdentityEvidenceError("unsupported_redesignation_current_class")
    header, primary, filer_cik, issuer, dates = _submission(
        raw,
        accession=reference["accession"],
        form="10-Q",
        filename=primary_filename,
        role="FILER",
        cutoff=cutoff,
    )
    files = re.findall(rb"(?m)^[ \t]*SEC FILE NUMBER:[ \t]*([^\r\n]+)", header)
    if (
        filer_cik != cik
        or files != [reference["sec_file_number"].encode()]
        or dates["filed_date"] != reference["referenced_filing_date"]
    ):
        raise IdentityEvidenceError("redesignation_filer_reference_mismatch")
    documents = re.findall(rb"<DOCUMENT>(.*?)</DOCUMENT>", raw, re.S | re.I)
    matches = [
        d
        for d in documents
        if re.findall(rb"(?im)^<FILENAME>[ \t]*([^\r\n]+)", d) == [reference["filename"].encode()]
    ]
    if len(matches) != 1 or re.findall(rb"(?im)^<TYPE>[ \t]*([^\r\n]+)", matches[0]) != [
        ("EX-" + reference["exhibit"]).encode()
    ]:
        raise IdentityEvidenceError("redesignation_exhibit_identity_mismatch")
    texts = re.findall(rb"<TEXT>(.*?)</TEXT>", matches[0], re.S | re.I)
    if len(texts) != 1:
        raise IdentityEvidenceError("ambiguous_redesignation_exhibit_text")
    text, primary_text = _text(texts[0]), _text(primary)
    names = re.findall(
        r"ARTICLE 1 N\s*AME The name of the corporation .{0,100}? is: (.{1,120}?) ARTICLE 2",
        text,
        re.I,
    )
    if len(names) != 1 or legal_name_key(names[0]) != legal_name_key(issuer):
        raise IdentityEvidenceError("redesignation_charter_named_issuer_mismatch")
    # Require a single current common authorization, separately preferred stock,
    # exact par values and a reconciled total. No missing authorized class is inferred.
    auth = re.findall(
        rf"The total number of shares of stock which the Corporation shall have authority to issue is ({_NUMBER}), "
        rf"consisting of ({_NUMBER}) shares of common stock, par value ({_PAR}) per share "
        rf"\(the {_QUOTE}Common Stock{_CLOSE}\) and ({_NUMBER}) shares of preferred stock, "
        rf"par value ({_PAR}) per share \(the {_QUOTE}Preferred Stock{_CLOSE}\)\.",
        text,
        re.I,
    )
    if len(auth) != 1:
        raise IdentityEvidenceError("unsupported_redesignation_authorized_classes")
    total, common, preferred = (int(auth[0][i].replace(",", "")) for i in (0, 1, 3))
    if total != common + preferred or _par_value(auth[0][2]) != _par_value(expected_title):
        raise IdentityEvidenceError("redesignation_authorized_counts_or_par_mismatch")
    # The operative provision must both redesignate each share one-for-one and
    # explicitly redirect all references to the old class. Partial quotations,
    # changed ratios, a different family or a different old letter are refused.
    provisions = list(
        re.finditer(
            rf"Effective upon the filing of this Amended and Restated Certificate of Incorporation, "
            rf"without any further action on the part of the Corporation or its stockholders, "
            rf"\(i\) each share of the Corporation[’']s Class ([A-Z0-9]+) common stock, par value ({_PAR}) "
            rf"per share \(the {_QUOTE}Class \1 Common Stock{_CLOSE}\) outstanding immediately prior thereto "
            rf"shall be redesignated as one share of Common Stock and \(ii\) all references to the Class \1 "
            rf"Common Stock or any right to purchase or acquire the Class \1 Common Stock "
            rf"\(whether in the Certificate of Incorporation or otherwise\) shall refer to the Common Stock\.",
            text,
            re.I,
        )
    )
    if len(provisions) != 1 or _par_value(provisions[0][2]) != _par_value(expected_title):
        raise IdentityEvidenceError("missing_or_conflicting_one_for_one_redesignation")
    letter = provisions[0][1].upper()
    effective = set(
        re.findall(
            rf"The amendments became effective upon the filing of the Third Amended and Restated Certificate "
            rf"of Incorporation with the Secretary of State of the State of Delaware on ({_DATE})\.",
            primary_text,
        )
    )
    if len(effective) != 1:
        raise IdentityEvidenceError("ambiguous_redesignation_effective_date")
    try:
        effective_day = datetime.strptime(next(iter(effective)), "%B %d, %Y").date().isoformat()
    except ValueError as error:
        raise IdentityEvidenceError("invalid_redesignation_effective_date") from error
    header_dates = re.findall(
        rf"THIRD AMENDED AND RESTATED CERTIFICATE OF INCORPORATION OF .{{1,120}}? ({_DATE})",
        text,
        re.I,
    )
    try:
        charter_day = (
            datetime.strptime(header_dates[0], "%B %d, %Y").date().isoformat()
            if len(header_dates) == 1
            else None
        )
    except ValueError as error:
        raise IdentityEvidenceError("invalid_redesignation_charter_date") from error
    if charter_day != effective_day or effective_day > dates["filed_date"]:
        raise IdentityEvidenceError("redesignation_charter_primary_effective_date_mismatch")
    if (
        len(
            re.findall(
                rf"rename the Company[’']s class {re.escape(letter)} common stock as {_QUOTE}common stock{_CLOSE}",
                primary_text,
                re.I,
            )
        )
        < 1
    ):
        raise IdentityEvidenceError("redesignation_primary_correspondence_missing")
    voting = re.findall(r"\(b\) Common Stock (.{1,2048}?) \(c\) Preferred Stock", text, re.I)
    if (
        len(voting) != 1
        or len(
            re.findall(
                r"each share of Common Stock outstanding on any record date shall be entitled to one vote",
                voting[0],
                re.I,
            )
        )
        != 1
        or re.search(r"non[ -]?voting", voting[0], re.I)
    ):
        raise IdentityEvidenceError("unsupported_redesignation_common_voting_rights")
    return {
        "issuer_cik": cik,
        "basis": "incorporated_issuer_charter_one_for_one_redesignation_and_all_references_directive",
        "old_class": letter,
        "new_class": "",
        "equity_family": "common",
        "ratio": "1:1",
        "common_votes_per_share": 1,
        "effective_date": effective_day,
        "common_par_value": str(_par_value(expected_title)),
        "authorized_common_shares": common,
        "authorized_preferred_shares": preferred,
        "accession": reference["accession"],
        "primary_filename": primary_filename,
        "charter_filename": reference["filename"],
        **dates,
        "issuer_name_sha256": _hash(legal_name_key(issuer).encode()),
        "complete_submission_sha256": _hash(raw),
        "SEC_header_sha256": _hash(header),
        "primary_sha256": _hash(primary),
        "charter_sha256": _hash(texts[0]),
        "operative_provision_sha256": _hash(provisions[0][0].encode()),
    }


def qualify_redesignated_class(
    periodic: dict[str, Any],
    ownership: dict[str, Any],
    proof: dict[str, Any],
) -> dict[str, Any]:
    if (
        periodic["issuer_cik"] != ownership["issuer_cik"]
        or proof["issuer_cik"] != periodic["issuer_cik"]
        or class_signature(periodic["security_title"]) != ("common", "", "")
        or class_signature(ownership["security_title"]) != ("common", proof["old_class"], "")
        or proof["ratio"] != "1:1"
        or proof["equity_family"] != "common"
        or str(_par_value(periodic["security_title"])) != proof["common_par_value"]
        or instant(ownership["accepted_at"]) <= instant(proof["available_by"])
    ):
        raise IdentityEvidenceError(
            "redesignation_cannot_override_other_issuer_class_family_rights_or_time"
        )
    if "$" in ownership["security_title"] and _par_value(ownership["security_title"]) != _par_value(
        periodic["security_title"]
    ):
        raise IdentityEvidenceError("redesignation_reported_ownership_par_conflict")
    return {
        **ownership,
        "security_title": periodic["security_title"],
        "available_by": max(
            instant(ownership["available_by"]), instant(proof["available_by"])
        ).isoformat(),
        "availability_basis": "maximum_of_ownership_and_incorporated_redesignation_evidence",
        "charter_redesignation_evidence": {
            **proof,
            "reported_ownership_title": ownership["security_title"],
            "reported_ownership_available_by": ownership["available_by"],
        },
    }
