"""Corroborate a missing class qualifier with dated, filed holdings tables.

The manager CIK never establishes issuer identity. That comes from a separate,
authenticated ownership subject, exact issuer name and check-digit-valid CUSIP.
This supports one literal common/nonvoting description, not family aliases,
continuous identity, market coverage or publication permission.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, time, timedelta
from typing import Any

from edgar_moe.data.historical_governance import _par_value
from edgar_moe.data.historical_identity import (
    MAX_DOCUMENT_BYTES,
    IdentityEvidenceError,
    class_signature,
    instant,
    valid_cusip,
)

FORM_NS = "http://www.sec.gov/edgar/thirteenffiler"
TABLE_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"


def normalized_name(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def _one(root: ET.Element, path: str) -> str:
    values = root.findall(path)
    if len(values) != 1 or values[0].text is None or list(values[0]):
        raise IdentityEvidenceError("missing_or_ambiguous_holdings_field")
    return values[0].text.strip()


def _xml(document: bytes, namespace: str, name: str) -> tuple[ET.Element, bytes]:
    texts = re.findall(rb"<TEXT>\s*<XML>(.*?)</XML>\s*</TEXT>", document, re.S | re.I)
    if len(texts) != 1 or re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", texts[0], re.I):
        raise IdentityEvidenceError("unsupported_holdings_XML_wrapper_or_entities")
    try:
        root = ET.fromstring(texts[0].strip())
    except ET.ParseError as error:
        raise IdentityEvidenceError("invalid_holdings_XML") from error
    if root.tag != f"{{{namespace}}}{name}":
        raise IdentityEvidenceError("holdings_XML_namespace_or_root_mismatch")
    count = 0
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > 24 or count > 250000:
            raise IdentityEvidenceError("holdings_XML_structure_bound")
        stack.extend((child, depth + 1) for child in node)
    return root, texts[0]


def filed_nonvoting_class(
    payload: bytes,
    reference: dict[str, Any],
    *,
    issuer_name: str,
    cusip: str,
    cutoff: datetime,
) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("invalid_holdings_submission_bytes")
    if not valid_cusip(cusip) or not normalized_name(issuer_name):
        raise IdentityEvidenceError("invalid_holdings_target_identity")
    metadata = reference["metadata"]
    manager = reference["manager_cik"]
    if (
        not isinstance(manager, str)
        or re.fullmatch(r"[0-9]{10}", manager) is None
        or int(manager) == 0
        or metadata["form"] != "13F-HR"
        or re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", metadata["accessionNumber"]) is None
        or hashlib.sha256(payload).hexdigest() != reference["document_sha256"]
    ):
        raise IdentityEvidenceError("holdings_reference_identity_or_digest_mismatch")
    headers = re.findall(rb"<SEC-HEADER>[^\n]*\n(.*?)</SEC-HEADER>", payload, re.S | re.I)
    if len(headers) != 1 or len(headers[0]) > 256 * 1024:
        raise IdentityEvidenceError("ambiguous_holdings_SEC_header")
    header = headers[0].replace(b"\r\n", b"\n")
    for field, expected in [
        ("ACCESSION NUMBER", metadata["accessionNumber"]),
        ("CONFORMED SUBMISSION TYPE", "13F-HR"),
    ]:
        if re.findall(rb"(?m)^" + field.encode() + rb":[ \t]*([^\n]+)", header) != [
            expected.encode()
        ]:
            raise IdentityEvidenceError("holdings_header_identity_mismatch")
    filers = re.findall(rb"(?ms)^FILER:\s*\n(.*?)(?=^\S|\Z)", header)
    if len(filers) != 1 or re.findall(rb"CENTRAL INDEX KEY:[ \t]*([0-9]+)", filers[0]) != [
        manager.encode()
    ]:
        raise IdentityEvidenceError("holdings_manager_identity_mismatch")
    filed = re.findall(rb"(?m)^FILED AS OF DATE:[ \t]*([0-9]{8})[ \t]*$", header)
    accepted = re.findall(rb"<ACCEPTANCE-DATETIME>([0-9]{14})", header)
    periods = re.findall(rb"(?m)^CONFORMED PERIOD OF REPORT:[ \t]*([0-9]{8})[ \t]*$", header)
    try:
        if len(filed) != 1 or len(accepted) != 1 or len(periods) != 1:
            raise ValueError("dates")
        filing_date = datetime.strptime(filed[0].decode(), "%Y%m%d").date()
        local_date = datetime.strptime(accepted[0].decode(), "%Y%m%d%H%M%S").date()
        period = datetime.strptime(periods[0].decode(), "%Y%m%d").date()
    except ValueError as error:
        raise IdentityEvidenceError("invalid_holdings_source_dates") from error
    available = datetime.combine(filing_date + timedelta(days=14), time.min, UTC)
    metadata_accepted = instant(metadata["acceptanceDateTime"])
    if (
        filing_date.isoformat() != metadata["filingDate"]
        or period.isoformat() != metadata["reportDate"]
        or not period <= local_date <= filing_date
        or not 0 <= (metadata_accepted.date() - local_date).days <= 1
        or metadata_accepted > cutoff
        or cutoff.utcoffset() is None
        or available > cutoff
    ):
        raise IdentityEvidenceError("holdings_source_unavailable_or_date_conflict")
    documents = re.findall(rb"<DOCUMENT>(.*?)</DOCUMENT>", payload, re.S | re.I)
    if len(documents) != 2:
        raise IdentityEvidenceError("unsupported_holdings_document_set")
    by_type = {}
    for doc in documents:
        kinds = re.findall(rb"(?im)^<TYPE>[ \t]*([^\r\n]+)", doc)
        if len(kinds) != 1 or kinds[0] in by_type:
            raise IdentityEvidenceError("ambiguous_holdings_document_types")
        by_type[kinds[0]] = doc
    if set(by_type) != {b"13F-HR", b"INFORMATION TABLE"}:
        raise IdentityEvidenceError("unsupported_holdings_document_types")
    primary, table = by_type[b"13F-HR"], by_type[b"INFORMATION TABLE"]
    if (
        re.findall(rb"(?im)^<FILENAME>[ \t]*([^\r\n]+)", primary) != [b"primary_doc.xml"]
        or re.findall(rb"(?im)^<FILENAME>[ \t]*([^\r\n]+)", table)
        != [reference["information_filename"].encode()]
        or metadata["primaryDocument"] not in {"primary_doc.xml", "xslForm13F_X01/primary_doc.xml"}
    ):
        raise IdentityEvidenceError("holdings_source_filename_mismatch")
    cover, _ = _xml(primary, FORM_NS, "edgarSubmission")
    ns = {"f": FORM_NS}
    fields = {
        "f:headerData/f:submissionType": "13F-HR",
        "f:headerData/f:filerInfo/f:liveTestFlag": "LIVE",
        "f:headerData/f:filerInfo/f:filer/f:credentials/f:cik": manager,
        "f:headerData/f:filerInfo/f:periodOfReport": period.strftime("%m-%d-%Y"),
        "f:formData/f:coverPage/f:reportCalendarOrQuarter": period.strftime("%m-%d-%Y"),
        "f:formData/f:coverPage/f:reportType": "13F HOLDINGS REPORT",
        "f:formData/f:summaryPage/f:isConfidentialOmitted": "false",
    }
    for path, expected in fields.items():
        if _one(cover, path.replace("f:", f"{{{FORM_NS}}}")) != expected:
            raise IdentityEvidenceError("holdings_cover_identity_or_completeness_mismatch")
    amendments = cover.findall("f:formData/f:coverPage/f:isAmendment", ns)
    if len(amendments) > 1 or any(node.text != "false" for node in amendments):
        raise IdentityEvidenceError("holdings_amendment_not_supported")
    xml, table_bytes = _xml(table, TABLE_NS, "informationTable")
    if any(row.tag != f"{{{TABLE_NS}}}infoTable" for row in xml):
        raise IdentityEvidenceError("unsupported_holdings_table_children")
    entries = _one(
        cover, f"{{{FORM_NS}}}formData/{{{FORM_NS}}}summaryPage/{{{FORM_NS}}}tableEntryTotal"
    )
    if (
        not entries.isascii()
        or not entries.isdecimal()
        or int(entries) != len(xml)
        or not 1 <= len(xml) <= 15000
    ):
        raise IdentityEvidenceError("holdings_table_count_mismatch")
    matching = []
    derivatives = 0
    prefix = f"{{{TABLE_NS}}}"
    for row in xml:
        identifiers = row.findall(prefix + "cusip")
        if not any(node.text == cusip for node in identifiers):
            continue
        if _one(row, prefix + "cusip") != cusip:
            raise IdentityEvidenceError("ambiguous_holdings_row_identifier")
        options = row.findall(prefix + "putCall")
        if options:
            if len(options) != 1 or options[0].text not in {"Put", "Call"}:
                raise IdentityEvidenceError("ambiguous_holdings_derivative_row")
            derivatives += 1
            continue
        if (
            normalized_name(_one(row, prefix + "nameOfIssuer")) != normalized_name(issuer_name)
            or _one(row, prefix + "titleOfClass") != "COM NON VTG"
            or _one(row, prefix + "shrsOrPrnAmt/" + prefix + "sshPrnamtType") != "SH"
        ):
            raise IdentityEvidenceError("holdings_issuer_name_or_class_conflict")
        matching.append(hashlib.sha256(ET.tostring(row)).hexdigest())
    if not matching:
        raise IdentityEvidenceError("missing_holdings_class_corroboration")
    return {
        "manager_cik": manager,
        "manager_CIK_used_as_issuer_identity": False,
        "accession": metadata["accessionNumber"],
        "report_date": period.isoformat(),
        "reported_local_acceptance_datetime": accepted[0].decode(),
        "reported_filing_date": filing_date.isoformat(),
        "available_by": available.isoformat(),
        "availability_basis": "SEC_header_filing_date_plus_fourteen_calendar_days_date_only",
        "document_sha256": reference["document_sha256"],
        "SEC_header_sha256": hashlib.sha256(headers[0]).hexdigest(),
        "information_table_sha256": hashlib.sha256(table_bytes).hexdigest(),
        "matched_issuer_name_sha256": hashlib.sha256(
            normalized_name(issuer_name).encode()
        ).hexdigest(),
        "cusip": cusip,
        "reported_class": "COM NON VTG",
        "class_signature": ["common", "", "nonvoting"],
        "matching_row_sha256s": sorted(matching),
        "derivative_rows_excluded": derivatives,
    }


def qualify_nonvoting_ownership(
    periodic: dict[str, Any], ownership: dict[str, Any], references: list[dict[str, Any]]
) -> dict[str, Any]:
    if (
        class_signature(periodic["security_title"]) != ("common", "", "nonvoting")
        or class_signature(ownership["security_title"]) != ("common", "", "")
        or periodic["issuer_cik"] != ownership["issuer_cik"]
        or len(references) != 2
        or len({r["report_date"] for r in references}) != 2
        or len({r["accession"] for r in references}) != 2
        or any(
            r["cusip"] != ownership["cusip"]
            or r["class_signature"] != ["common", "", "nonvoting"]
            or r["manager_CIK_used_as_issuer_identity"] is not False
            or instant(r["available_by"]) > instant(ownership["accepted_at"])
            for r in references
        )
        or (
            "$" in ownership["security_title"]
            and _par_value(ownership["security_title"]) != _par_value(periodic["security_title"])
        )
    ):
        raise IdentityEvidenceError("unbound_holdings_class_ownership_qualifier")
    return {
        **ownership,
        "reported_security_title": ownership["security_title"],
        "security_title": "Non-voting " + ownership["security_title"],
        "class_qualifier_basis": "two_dated_filed_tables_same_authenticated_issuer_name_and_CUSIP",
    }
