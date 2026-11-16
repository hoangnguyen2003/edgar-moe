"""Refine a missing named class using an issuer's exact filed ownership citation.

This narrow two-power Schedule 13G format does not equate common/ordinary stock,
override a reported class or voting right, or prove historical market continuity.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, time, timedelta
from typing import Any

from bs4 import BeautifulSoup

from edgar_moe.data.historical_class_charter import _name as legal_name_key
from edgar_moe.data.historical_identity import (
    MAX_DOCUMENT_BYTES,
    IdentityEvidenceError,
    _ownership_subject_name,
    class_signature,
    instant,
    ownership_identity,
)

_NUMBER = r"(?:0|[1-9][0-9]*)(?:,[0-9]{3})*"
_DATE = r"[A-Za-z]+ [0-9]{1,2}, [0-9]{4}"
_TITLE = r"Class [A-Z0-9]+ common stock"


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _submission(
    raw: bytes, *, accession: str, form: str, filename: str, role: str, cutoff: datetime
) -> tuple[bytes, bytes, str, str, dict[str, str]]:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("invalid_proxy_reference_submission_bytes")
    if re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession) is None or role not in {
        "FILER",
        "FILED BY",
    }:
        raise IdentityEvidenceError("invalid_proxy_reference_submission_identity")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.(?:htm|html|txt)", filename) is None:
        raise IdentityEvidenceError("invalid_proxy_reference_filename")
    headers = re.findall(rb"<SEC-HEADER>(.*?)</SEC-HEADER>", raw, re.S | re.I)
    if (
        len(headers) != 1
        or len(re.findall(rb"<SEC-HEADER>", raw, re.I)) != 1
        or len(re.findall(rb"</SEC-HEADER>", raw, re.I)) != 1
        or len(headers[0]) > 256 * 1024
    ):
        raise IdentityEvidenceError("ambiguous_proxy_reference_header")
    header = headers[0].replace(b"\r\n", b"\n")
    if re.findall(rb"(?m)^ACCESSION NUMBER:[ \t]*([^\n]+)", header) != [accession.encode()]:
        raise IdentityEvidenceError("proxy_reference_accession_mismatch")
    if re.findall(rb"(?m)^CONFORMED SUBMISSION TYPE:[ \t]*([^\n]+)", header) != [form.encode()]:
        raise IdentityEvidenceError("proxy_reference_form_mismatch")
    roles = re.findall(rb"(?ms)^" + role.encode() + rb":\s*\n(.*?)(?=^\S|\Z)", header)
    if len(roles) != 1:
        raise IdentityEvidenceError("ambiguous_proxy_reference_filer")
    names = re.findall(rb"COMPANY CONFORMED NAME:[ \t]*([^\n]+)", roles[0])
    ciks = re.findall(rb"CENTRAL INDEX KEY:[ \t]*([0-9]+)", roles[0])
    if len(names) != 1 or len(ciks) != 1 or not 0 < int(ciks[0]) < 10**10:
        raise IdentityEvidenceError("invalid_proxy_reference_filer")
    filed = re.findall(rb"(?m)^FILED AS OF DATE:[ \t]*([0-9]{8})[ \t]*$", header)
    accepted = re.findall(rb"<ACCEPTANCE-DATETIME>([0-9]{14})", header)
    if len(filed) != 1 or len(accepted) != 1:
        raise IdentityEvidenceError("missing_proxy_reference_dates")
    try:
        filed_date = datetime.strptime(filed[0].decode(), "%Y%m%d").date()
        accepted_date = datetime.strptime(accepted[0].decode(), "%Y%m%d%H%M%S").date()
    except ValueError as error:
        raise IdentityEvidenceError("invalid_proxy_reference_dates") from error
    available = datetime.combine(filed_date + timedelta(days=14), time.min, UTC)
    if cutoff.utcoffset() is None or accepted_date > filed_date or available > cutoff:
        raise IdentityEvidenceError("proxy_reference_not_available_by_cutoff")
    documents = re.findall(rb"<DOCUMENT>(.*?)</DOCUMENT>", raw, re.S | re.I)
    if not 1 <= len(documents) <= 128:
        raise IdentityEvidenceError("invalid_proxy_reference_document_set")
    matches = [
        d
        for d in documents
        if re.findall(rb"(?im)^<FILENAME>[ \t]*([^\r\n]+)", d) == [filename.encode()]
    ]
    if len(matches) != 1:
        raise IdentityEvidenceError("ambiguous_proxy_reference_primary")
    document = matches[0]
    texts = re.findall(rb"<TEXT>(.*?)</TEXT>", document, re.S | re.I)
    if re.findall(rb"(?im)^<TYPE>[ \t]*([^\r\n]+)", document) != [form.encode()] or len(texts) != 1:
        raise IdentityEvidenceError("invalid_proxy_reference_primary_type_or_text")
    return (
        header,
        texts[0],
        ciks[0].decode().zfill(10),
        names[0].decode("utf-8"),
        {
            "filed_date": filed_date.isoformat(),
            "reported_local_acceptance_datetime": accepted[0].decode(),
            "available_by": available.isoformat(),
            "availability_basis": "SEC_header_filing_date_plus_fourteen_calendar_days_date_only",
        },
    )


def qualify_proxy_class(
    periodic: dict[str, Any],
    ownership_raw: bytes,
    ownership_metadata: dict[str, Any],
    proxy_raw: bytes,
    *,
    proxy_accession: str,
    proxy_filename: str,
    cutoff: datetime,
) -> dict[str, Any]:
    """Return one source-qualified ownership record; baseline caller replays the cover."""
    cik = periodic["issuer_cik"]
    subject_name, subject = _ownership_subject_name(ownership_raw, ownership_metadata, cik)
    own = ownership_identity(ownership_raw, ownership_metadata, cik=cik, cutoff=cutoff)
    registered, reported = (
        class_signature(periodic["security_title"]),
        class_signature(own["security_title"]),
    )
    # Only a missing class letter within the same common-stock family is refined.
    if registered[0] != "common" or not registered[1] or reported != ("common", "", registered[2]):
        raise IdentityEvidenceError("proxy_cannot_override_family_class_or_voting")
    header, primary, proxy_cik, issuer, proxy_dates = _submission(
        proxy_raw,
        accession=proxy_accession,
        form="DEF 14A",
        filename=proxy_filename,
        role="FILER",
        cutoff=cutoff,
    )
    if proxy_cik != cik or legal_name_key(issuer) != legal_name_key(subject_name.decode("utf-8")):
        raise IdentityEvidenceError("proxy_reference_subject_issuer_mismatch")
    own_header, own_primary, reporter_cik, reporter, own_dates = _submission(
        ownership_raw,
        accession=own["accession"],
        form="SC 13G/A",
        filename=ownership_metadata["primaryDocument"],
        role="FILED BY",
        cutoff=cutoff,
    )
    if own_dates["filed_date"] > proxy_dates["filed_date"] or instant(
        own["available_by"]
    ) > instant(proxy_dates["available_by"]):
        raise IdentityEvidenceError("proxy_cites_unavailable_ownership")
    text = " ".join(BeautifulSoup(primary, "html.parser").get_text(" ", strip=True).split())
    own_text = " ".join(BeautifulSoup(own_primary, "html.parser").get_text(" ", strip=True).split())
    aliases = set()
    for match in re.finditer(
        rf"shares of ({_TITLE}) of ([^()\n]{{1,120}}) \([“\"]([A-Za-z]{{2,30}})[”\"]\)", text, re.I
    ):
        if (
            legal_name_key(match[2]) == legal_name_key(issuer)
            and class_signature(match[1]) == registered
        ):
            aliases.add(match[3])
    if len(aliases) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_proxy_issuer_alias")
    alias = next(iter(aliases))
    amendments = re.findall(r"\(Amendment No[.:]?\s*([0-9]+)\)", own_text, re.I)
    powers = re.findall(
        rf"\(5\) Sole voting power ({_NUMBER}) \(6\) Shared voting power ({_NUMBER}) "
        rf"\(7\) Sole dispositive power ({_NUMBER}) \(8\) Shared dispositive power ({_NUMBER}) "
        rf"\(9\) Aggregate amount beneficially owned by each reporting person ({_NUMBER}) \(10\)",
        own_text,
        re.I,
    )
    if len(amendments) != 1 or len(powers) != 1:
        raise IdentityEvidenceError("ambiguous_proxy_ownership_amendment_or_powers")
    counts = tuple(int(v.replace(",", "")) for v in powers[0])
    if counts[1] != 0 or counts[3] != 0 or not 0 <= counts[0] <= counts[2] == counts[4] < 10**12:
        raise IdentityEvidenceError("unsupported_proxy_ownership_power_structure")
    pattern = (
        rf"According to the Schedule 13G \(Amendment No\. ([0-9]+)\) filed on ({_DATE}), "
        rf"by ([^()\n]{{1,120}}) \([“\"]([A-Za-z]{{2,30}})[”\"]\), of the ({_NUMBER}) shares of "
        rf"{re.escape(alias)}[’']s ({_TITLE}) beneficially owned, \4 has \(a\) sole voting power "
        rf"with respect to ({_NUMBER}) shares, and \(b\) sole investment power with respect to "
        rf"all ({_NUMBER}) shares\."
    )
    references = [
        m
        for m in re.finditer(pattern, text, re.I)
        if legal_name_key(m[3]) == legal_name_key(reporter)
    ]
    if len(references) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_issuer_ownership_citation")
    match = references[0]
    try:
        cited_date = datetime.strptime(match[2], "%B %d, %Y").date().isoformat()
    except ValueError as error:
        raise IdentityEvidenceError("invalid_proxy_citation_date") from error
    cited_counts = tuple(int(match[i].replace(",", "")) for i in (5, 7, 8))
    if (
        match[1] != amendments[0]
        or cited_date != own_dates["filed_date"]
        or cited_counts != (counts[4], counts[0], counts[2])
        or class_signature(match[6]) != registered
    ):
        raise IdentityEvidenceError("proxy_citation_class_date_or_position_mismatch")
    return {
        **own,
        "security_title": f"Class {registered[1]} " + own["security_title"],
        "available_by": max(
            instant(own["available_by"]), instant(proxy_dates["available_by"])
        ).isoformat(),
        "availability_basis": "maximum_of_ownership_and_issuer_proxy_class_evidence",
        "issuer_proxy_class_evidence": {
            "basis": "issuer_filed_exact_13G_amendment_date_reporter_and_position_citation",
            "reported_ownership_title": own["security_title"],
            "reported_ownership_available_by": own["available_by"],
            "proxy_accession": proxy_accession,
            "proxy_primary_filename": proxy_filename,
            **proxy_dates,
            "complete_proxy_submission_sha256": _hash(proxy_raw),
            "proxy_SEC_header_sha256": _hash(header),
            "proxy_primary_sha256": _hash(primary),
            "citation_sha256": _hash(match[0].encode()),
            "reporter_cik": reporter_cik,
            "reporter_SEC_header_sha256": _hash(own_header),
            "ownership_subject_evidence": subject,
            "proxy_reported_ownership_filing_date": cited_date,
            "proxy_reported_amendment": amendments[0],
        },
    }
