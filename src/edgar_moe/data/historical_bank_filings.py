"""Offline FDIC periodic reports joined to dated SEC ownership by name and EIN.

No bank identity is inferred from a ticker, current institution directory, logo,
PDF filename or a present-day surviving institution. Original raw sources stay
private, and this evidence does not establish market-security continuity.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from edgar_moe.data.historical_class_charter import _name as legal_name_key
from edgar_moe.data.historical_identity import (
    IdentityEvidenceError,
    _ownership_subject_name,
    instant,
    join_identity,
    ownership_identity,
)
from edgar_moe.data.screen_inputs import canonical_bytes, decode_json

MAX_PDF_BYTES = 16 * 1024**2
MAX_INDEX_BYTES = 8 * 1024**2
PDF_VERSION = "7.1.0"
ANCHOR_STATUS = "pre_cutoff_FDIC_SEC_identity_anchored"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def decoder_identity() -> str:
    """Bind the reviewed pure-Python decoder's version and installed source bytes."""
    identities = {}
    for package, version, prefix in [
        ("pypdf", PDF_VERSION, "pypdf/"),
        ("fonttools", "4.68.0", "fontTools/"),
    ]:
        distribution = importlib.metadata.distribution(package)
        if distribution.version != version:
            raise IdentityEvidenceError("unreviewed_bank_PDF_decoder_version")
        sources = {
            str(p): digest(Path(str(distribution.locate_file(p))).read_bytes())
            for p in distribution.files or []
            if str(p).startswith(prefix) and str(p).endswith(".py")
        }
        if not 10 <= len(sources) <= 2048:
            raise IdentityEvidenceError("missing_bank_PDF_decoder_sources")
        identities[package] = {"version": version, "sources": sources}
    return digest(canonical_bytes(identities))


def day(value: Any) -> date:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise IdentityEvidenceError("invalid_FDIC_filing_date")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise IdentityEvidenceError("invalid_FDIC_filing_date") from error


def bounded_json(raw: bytes, maximum: int) -> Any:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
        raise IdentityEvidenceError("invalid_FDIC_source_size")
    return decode_json(raw)


def select_filing(index_raw: bytes, cert: int, cutoff: datetime) -> dict[str, Any]:
    rows = bounded_json(index_raw, MAX_INDEX_BYTES)
    if (
        type(cert) is not int
        or not 0 < cert < 1000000
        or not isinstance(rows, list)
        or not 1 <= len(rows) <= 2000
    ):
        raise IdentityEvidenceError("invalid_FDIC_certificate_index")
    candidates = []
    ids = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or type(row.get("orgCertNum")) is not int
            or row.get("orgCertNum") != cert
            or type(row.get("instFlngId")) is not int
            or row["instFlngId"] <= 0
            or row["instFlngId"] in ids
        ):
            raise IdentityEvidenceError("FDIC_index_issuer_or_filing_identity_conflict")
        ids.add(row["instFlngId"])
        form = row.get("instFlngForm", {})
        if (
            not isinstance(form, dict)
            or form.get("instFlngFormNme") != "Form 10-Q~Quarterly Report"
        ):
            continue
        filed = day(row.get("sysAddRecDttm"))
        if datetime.combine(filed + timedelta(days=14), time.min, UTC) <= cutoff:
            candidates.append(row)
    if not candidates:
        raise IdentityEvidenceError("no_pre_cutoff_FDIC_quarterly_report")
    latest = max(day(row["sysAddRecDttm"]) for row in candidates)
    selected = [row for row in candidates if day(row["sysAddRecDttm"]) == latest]
    if len(selected) != 1:
        raise IdentityEvidenceError("ambiguous_latest_FDIC_quarterly_report")
    return selected[0]


def filing_metadata(
    selected: dict[str, Any], details_raw: bytes, cutoff: datetime
) -> dict[str, Any]:
    details = bounded_json(details_raw, 256 * 1024)
    if canonical_bytes(details) != canonical_bytes(selected):
        raise IdentityEvidenceError("FDIC_index_detail_payload_mismatch")
    form = details["instFlngForm"]
    if form.get("instFlngFormConfInd") != "N" or details.get("instFlngCmpltCde") != "S":
        raise IdentityEvidenceError("unsupported_FDIC_filing_status")
    filed = day(details["sysAddRecDttm"])
    record_changed = day(details.get("sysLstActDttm"))
    event_day = day(details.get("instFlngEvtCausDttm"))
    if not filed <= event_day <= record_changed:
        raise IdentityEvidenceError("FDIC_record_date_order_conflict")
    name = details.get("instFincLglName")
    if (
        not isinstance(name, str)
        or not 3 <= len(name) <= 120
        or not re.fullmatch(r"[A-Za-z0-9 .,'&()-]+", name)
    ):
        raise IdentityEvidenceError("invalid_FDIC_reported_issuer_name")
    attachments = details.get("instFlngAtchList")
    if not isinstance(attachments, list) or len(attachments) != 1:
        raise IdentityEvidenceError("ambiguous_FDIC_report_attachment")
    item = attachments[0]
    if (
        not isinstance(item, dict)
        or item.get("instFlngId") != details["instFlngId"]
        or type(item.get("instFlngAtchId")) is not int
        or item["instFlngAtchId"] <= 0
        or item.get("instFlngAtchAmndCde") != "O"
        or item.get("instFlngAtchStatCde") != "S"
        or type(item.get("instFlngAtchSizeQty")) is not int
        or not 0 < item["instFlngAtchSizeQty"] <= MAX_PDF_BYTES
        or not isinstance(item.get("instFlngAtchOrglNme"), str)
        or not item["instFlngAtchOrglNme"].lower().endswith(".pdf")
    ):
        raise IdentityEvidenceError("unsupported_FDIC_original_PDF_attachment")
    created, changed = instant(item.get("instFlngAtchCrteDttm")), instant(item.get("sysLstActDttm"))
    filed_start = datetime.combine(filed, time.min, UTC)
    if not filed_start <= created <= changed:
        raise IdentityEvidenceError("FDIC_attachment_creation_or_change_date_conflict")
    available = max(
        filed_start,
        created,
        changed,
        datetime.combine(record_changed + timedelta(days=1), time.min, UTC),
        datetime.combine(event_day + timedelta(days=1), time.min, UTC),
    ) + timedelta(days=14)
    if available > cutoff:
        raise IdentityEvidenceError("FDIC_attachment_not_available_by_cutoff")
    return {
        "regulator": "FDIC",
        "FDIC_form": form["instFlngFormNme"],
        "certificate": details["orgCertNum"],
        "filing_id": details["instFlngId"],
        "attachment_id": item["instFlngAtchId"],
        "reported_issuer_name": name,
        "reported_filing_date": filed.isoformat(),
        "reported_record_change_date": record_changed.isoformat(),
        "reported_event_date": event_day.isoformat(),
        "accepted_at": created.isoformat(),
        "attachment_last_changed_at": changed.isoformat(),
        "available_by": available.isoformat(),
        "availability_basis": "maximum_FDIC_filing_record_event_day_end_attachment_creation_and_change_plus_fourteen_calendar_days",
        "expected_bytes": item["instFlngAtchSizeQty"],
        "index_row_sha256": digest(canonical_bytes(selected)),
        "detail_sha256": digest(details_raw),
    }


def pdf_pages(raw: bytes, *, all_pages: bool = False) -> tuple[dict[int, str], int]:
    from pypdf import PdfReader

    if (
        not isinstance(raw, bytes)
        or not 0 < len(raw) <= MAX_PDF_BYTES
        or not raw.startswith(b"%PDF-")
    ):
        raise IdentityEvidenceError("invalid_bank_PDF_bytes")
    try:
        reader = PdfReader(
            io.BytesIO(raw),
            strict=True,
            root_object_recovery_limit=0,
            xref_stream_maximum_entries=200000,
        )
        if reader.is_encrypted or not 2 <= len(reader.pages) <= 400:
            raise IdentityEvidenceError("encrypted_or_excessive_bank_PDF")
        indices = sorted(
            set(range(min(8, len(reader.pages))))
            | set(range(max(0, len(reader.pages) - 8), len(reader.pages)))
        )
        if all_pages:
            indices = list(range(len(reader.pages)))
        result = {}
        stream_bytes = 0
        text_bytes = 0
        for i in indices:
            page = reader.pages[i]
            content = page.get_contents()
            if content is None or len(content.get_data()) > 2 * 1024**2:
                raise IdentityEvidenceError("empty_or_excessive_bank_PDF_page_stream")
            stream_bytes += len(content.get_data())
            if stream_bytes > 32 * 1024**2:
                raise IdentityEvidenceError("bank_PDF_total_stream_bound")
            text = page.extract_text(extraction_mode="layout")
            if not isinstance(text, str) or not 0 < len(text) <= 1024**2:
                raise IdentityEvidenceError("missing_or_excessive_bank_PDF_page_text")
            text_bytes += len(text.encode())
            if text_bytes > 16 * 1024**2:
                raise IdentityEvidenceError("bank_PDF_total_text_bound")
            result[i] = "\n".join(" ".join(line.split()) for line in text.splitlines())
        return result, len(reader.pages)
    except IdentityEvidenceError:
        raise
    except Exception as error:
        raise IdentityEvidenceError("bank_PDF_decoder_refused") from error


def periodic_report(
    raw: bytes, metadata: dict[str, Any], *, cik: str, symbol: str, cutoff: datetime
) -> dict[str, Any]:
    if (
        re.fullmatch(r"[0-9]{10}", cik) is None
        or int(cik) == 0
        or re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol) is None
    ):
        raise IdentityEvidenceError("invalid_bank_snapshot_identity")
    if len(raw) != metadata["expected_bytes"] or instant(metadata["available_by"]) > cutoff:
        raise IdentityEvidenceError("bank_PDF_size_or_availability_conflict")
    pages, count = pdf_pages(raw)
    cover = pages[0]
    flat = " ".join(cover.split())
    if (
        "FEDERAL DEPOSIT INSURANCE CORPORATION" not in flat
        or len(re.findall(r"\bFORM 10-Q\b", flat, re.I)) != 1
    ):
        raise IdentityEvidenceError("bank_cover_regulator_or_form_mismatch")
    dates = re.findall(
        r"For the quarterly period ended:? ([A-Za-z]+ [0-9]{1,2}, [0-9]{4})", flat, re.I
    )
    try:
        period = datetime.strptime(dates[0], "%B %d, %Y").date() if len(dates) == 1 else None
    except ValueError as error:
        raise IdentityEvidenceError("invalid_bank_quarter_end") from error
    filed = day(metadata["reported_filing_date"])
    if (
        period is None
        or (period.month, period.day) not in {(3, 31), (6, 30), (9, 30)}
        or not 0 < (filed - period).days <= 90
    ):
        raise IdentityEvidenceError("bank_quarter_end_filing_date_conflict")
    eins = set(re.findall(r"\b([0-9]{2}-[0-9]{7})\b", cover))
    if len(eins) != 1 or "00-0000000" in eins:
        raise IdentityEvidenceError("missing_or_ambiguous_bank_cover_EIN")
    name_key = legal_name_key(metadata["reported_issuer_name"])
    named_pages = []
    for i, text in pages.items():
        if i >= 8:
            continue
        lines = [line for line in text.splitlines() if line]
        for j, line in enumerate(lines[:-1]):
            if legal_name_key(line) == name_key and re.match(
                r"(?:FORM 10-Q|CONSOLIDATED |\(Exact name)", lines[j + 1], re.I
            ):
                named_pages.append(i + 1)
    if not named_pages:
        raise IdentityEvidenceError("bank_PDF_named_registrant_not_found")
    certificates = re.findall(r"FDIC Certificate Number\s+([0-9]+)", flat, re.I)
    if certificates and certificates != [str(metadata["certificate"])]:
        raise IdentityEvidenceError("bank_cover_certificate_conflict")
    table = re.findall(
        r"Securities registered pursuant to Section 12\(b\) of the Act:(.*?)Indicate by check mark",
        flat,
        re.I,
    )
    if len(table) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_bank_registration_table")
    rows = []
    for line in cover.splitlines():
        if line not in table[0] or symbol not in line:
            continue
        match = re.fullmatch(
            rf"(Common Stock, \$[0-9]+(?:\.[0-9]+)? par value(?: per share)?) {re.escape(symbol)} (New York Stock Exchange|NASDAQ Global Select Market)",
            line,
            re.I,
        )
        if match:
            rows.append((match[1], match[2]))
        elif (
            re.search(rf"(?:^|\s){re.escape(symbol)}(?:\s|$)", line)
            and "Title of each class" not in line
        ):
            raise IdentityEvidenceError("unsupported_or_conflicting_bank_registration_row")
    if len(rows) != 1:
        raise IdentityEvidenceError("missing_or_duplicate_bank_common_registration")
    tail = " ".join(pages[i] for i in pages if i >= max(0, count - 8))
    signature_dates = set(re.findall(r"Date:\s*([A-Za-z]+ [0-9]{1,2}, [0-9]{4})", tail))
    if not re.search(r"\bSIGNATURES\b", tail) or not signature_dates:
        raise IdentityEvidenceError("missing_bank_report_signatures")
    try:
        signed = {datetime.strptime(value, "%B %d, %Y").date() for value in signature_dates}
    except ValueError as error:
        raise IdentityEvidenceError("invalid_bank_report_signature_date") from error
    if signed != {filed}:
        raise IdentityEvidenceError("bank_signature_and_regulator_filing_date_conflict")
    return {
        **metadata,
        "issuer_cik": cik,
        "form": "10-Q",
        "accession": f"FDIC:{metadata['certificate']}:{metadata['filing_id']}:{metadata['attachment_id']}",
        "symbol_at_cutoff": symbol,
        "security_title": rows[0][0],
        "exchange_at_filing": rows[0][1],
        "document_sha256": digest(raw),
        "report_period_end": period.isoformat(),
        "report_signature_date": filed.isoformat(),
        "EIN": next(iter(eins)).replace("-", ""),
        "PDF_page_count": count,
        "named_registrant_pages": sorted(set(named_pages)),
        "PDF_cover_text_sha256": digest(cover.encode()),
        "PDF_decoder_version": PDF_VERSION,
    }


def join_bank_ownership(
    periodic: dict[str, Any], raw: bytes, row: dict[str, Any], *, cutoff: datetime
) -> dict[str, Any]:
    cik = periodic["issuer_cik"]
    name, subject = _ownership_subject_name(raw, row, cik)
    headers = re.findall(rb"<SEC-HEADER>(.*?)</SEC-HEADER>", raw, re.S | re.I)
    blocks = re.findall(
        rb"(?ms)^SUBJECT COMPANY:\s*\n(.*?)(?=^\S|\Z)", headers[0].replace(b"\r\n", b"\n")
    )
    eins = re.findall(rb"(?m)^[ \t]*IRS NUMBER:[ \t]*([0-9]{9})[ \t]*$", blocks[0])
    if legal_name_key(name.decode("utf-8")) != legal_name_key(
        periodic["reported_issuer_name"]
    ) or eins != [periodic["EIN"].encode()]:
        raise IdentityEvidenceError("SEC_subject_and_FDIC_bank_name_or_EIN_conflict")
    own = ownership_identity(raw, row, cik=cik, cutoff=cutoff)
    own["ownership_subject_evidence"] = subject
    result = join_identity(periodic, own)
    result["identity_basis"] = (
        "FDIC_periodic_and_SEC_subject_name_EIN_and_share_class_with_pre_cutoff_reported_CUSIP"
    )
    return result


def qualify_bank_proxy(
    periodic: dict[str, Any],
    raw: bytes,
    row: dict[str, Any],
    proxy_raw: bytes,
    proxy: dict[str, Any],
    *,
    cutoff: datetime,
) -> dict[str, Any]:
    """An explicit FDIC issuer citation is separate from an unavailable EIN join.

    The subject's reported tax identifier is preserved as unreconciled metadata.
    It is never asserted equal or silently used as a bank-identity key. The
    issuer's own filed citation must bind the reporter, date, event, address,
    aggregate and all four voting/dispositive positions to its common stock.
    """
    from bs4 import BeautifulSoup

    from edgar_moe.data.historical_identity import class_signature
    from edgar_moe.data.historical_proxy_class import _submission

    name, subject = _ownership_subject_name(raw, row, periodic["issuer_cik"])
    if (
        legal_name_key(name.decode()) != legal_name_key(periodic["reported_issuer_name"])
        or proxy["certificate"] != periodic["certificate"]
        or legal_name_key(proxy["reported_issuer_name"])
        != legal_name_key(periodic["reported_issuer_name"])
        or proxy["FDIC_form"] != "Definitive Proxy/Information Statements"
        or len(proxy_raw) != proxy["expected_bytes"]
        or instant(proxy["available_by"]) > cutoff
        or class_signature(periodic["security_title"]) != ("common", "", "")
    ):
        raise IdentityEvidenceError("FDIC_proxy_periodic_subject_or_class_conflict")
    own = ownership_identity(raw, row, cik=periodic["issuer_cik"], cutoff=cutoff)
    if class_signature(own["security_title"]) != ("common", "", ""):
        raise IdentityEvidenceError("FDIC_proxy_cannot_override_reported_class_family_or_voting")
    header, primary, reporter_cik, reporter, own_dates = _submission(
        raw,
        accession=own["accession"],
        form="SC 13G/A",
        filename=row["primaryDocument"],
        role="FILED BY",
        cutoff=cutoff,
    )
    if own_dates["filed_date"] > proxy["reported_filing_date"] or instant(
        own_dates["available_by"]
    ) > instant(proxy["available_by"]):
        raise IdentityEvidenceError("FDIC_proxy_cites_unavailable_ownership")
    text = " ".join(BeautifulSoup(primary, "html.parser").get_text(" ", strip=True).split())
    display = re.findall(
        r"1\. NAME OF REPORTING PERSON I\.R\.S\. IDENTIFICATION NO\. OF ABOVE PERSON (.{1,120}?) - ([0-9]{2}-[0-9]{7}) 2\.",
        text,
        re.I,
    )
    reporter_blocks = re.findall(rb"(?ms)^FILED BY:\s*\n(.*?)(?=^\S|\Z)", header)
    reporter_eins = (
        re.findall(rb"(?m)^[ \t]*IRS NUMBER:[ \t]*([0-9]{9})[ \t]*$", reporter_blocks[0])
        if len(reporter_blocks) == 1
        else []
    )
    if (
        len(display) != 1
        or len(reporter_eins) != 1
        or reporter_eins[0] == b"000000000"
        or display[0][1].replace("-", "") != reporter_eins[0].decode()
    ):
        raise IdentityEvidenceError("FDIC_proxy_reporter_cover_header_identity_conflict")
    address = re.findall(
        r"Item 2\(b\) [–-] Address of Principal Business Office or, if none, residence: (.{1,180}?) Item 2\(c\)",
        text,
        re.I,
    )
    date_pattern = r"[A-Za-z]+ [0-9]{1,2}, [0-9]{4}"
    events = re.findall(
        rf"Date of Event Which Requires Filing of this Statement: ({date_pattern})", text, re.I
    )
    number = r"(?:0|[1-9][0-9]*)(?:,[0-9]{3})*"
    positions = re.findall(
        rf"5\. SOLE VOTING POWER ({number}) 6\. SHARED VOTING POWER ({number}) 7\. SOLE DISPOSITIVE POWER ({number}) 8\. SHARED DISPOSITIVE POWER ({number}) 9\. AGGREGATE AMOUNT BENEFICIALLY OWNED BY EACH REPORTING PERSON ({number}) 10\.",
        text,
        re.I,
    )
    if len(address) != 1 or len(events) != 1 or len(positions) != 1:
        raise IdentityEvidenceError("ambiguous_FDIC_proxy_ownership_fields")
    counts = tuple(int(v.replace(",", "")) for v in positions[0])
    if not (
        counts[0] == 0
        and 0 < counts[1] <= counts[4] < 10**12
        and counts[2] > 0
        and counts[3] > 0
        and counts[2] + counts[3] == counts[4]
    ):
        raise IdentityEvidenceError("unsupported_FDIC_proxy_ownership_position_structure")
    pages, _ = pdf_pages(proxy_raw, all_pages=True)
    first = " ".join(pages[i] for i in pages if i < 8)
    bank = re.escape(periodic["reported_issuer_name"])
    if (
        not re.search(r"FEDERAL DEPOSIT INSURANCE CORPORATION", first)
        or not re.search(r"SCHEDULE 14A", first, re.I)
        or not re.search(
            rf"{bank}\s+\(Name of Registrant as Specified In Its Charter\)", first, re.I
        )
    ):
        raise IdentityEvidenceError("FDIC_proxy_named_registrant_or_form_conflict")
    ownership_pages = [
        (i, p)
        for i, p in pages.items()
        if p.lstrip().startswith("Security Ownership of Certain Beneficial Owners\n")
    ]
    if len(ownership_pages) != 1:
        raise IdentityEvidenceError("ambiguous_FDIC_proxy_ownership_page")
    page_index, page = ownership_pages[0]
    if not re.search(r"beneficial ownership of the common stock as of ", page, re.I):
        raise IdentityEvidenceError("FDIC_proxy_table_class_missing")
    flat = " ".join(page.split())
    display_name = display[0][0]
    pattern = rf"\(([1-9][0-9]?)\) This information is derived from the Schedule 13G/A, filed by {re.escape(display_name)} on ({date_pattern}), which states that {re.escape(display_name)} has sole voting power over no shares, shared voting power over ({number}) shares, sole dispositive power over ({number}) shares and shared dispositive power over ({number}) shares, as of ({date_pattern})\."
    citations = list(re.finditer(pattern, flat, re.I))
    if len(citations) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_FDIC_proxy_exact_citation")
    cite = citations[0]
    table_blocks = [
        b for b in re.split(r"\n\s*\n+", page) if re.match(re.escape(display_name) + r" \(", b)
    ]
    table = []
    for block in table_blocks:
        match = re.fullmatch(
            rf"{re.escape(display_name)} \(([1-9][0-9]?)\) ({number}) ([0-9]+(?:\.[0-9]+)?)%\n(.{{1,180}}\n.{{1,180}})",
            block,
        )
        if match:
            table.append(match)

    def key(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    try:
        cited_day, event_day, cover_day = [
            datetime.strptime(v, "%B %d, %Y").date().isoformat()
            for v in (cite[2], cite[6], events[0])
        ]
    except ValueError as error:
        raise IdentityEvidenceError("invalid_FDIC_proxy_citation_dates") from error
    if (
        len(table) != 1
        or table[0][1] != cite[1]
        or int(table[0][2].replace(",", "")) != counts[4]
        or key(table[0][4]) != key(address[0])
        or cited_day != own_dates["filed_date"]
        or event_day != cover_day
        or event_day > cited_day
        or tuple(int(cite[i].replace(",", "")) for i in (3, 4, 5))
        != (counts[1], counts[2], counts[3])
    ):
        raise IdentityEvidenceError(
            "FDIC_proxy_citation_address_dates_marker_or_positions_conflict"
        )
    subject_block = re.findall(rb"(?ms)^SUBJECT COMPANY:\s*\n(.*?)(?=^\S|\Z)", header)[0]
    subject_eins = re.findall(rb"(?m)^[ \t]*IRS NUMBER:[ \t]*([0-9]{9})[ \t]*$", subject_block)
    own["ownership_subject_evidence"] = subject
    own["FDIC_issuer_citation_evidence"] = {
        **proxy,
        "document_sha256": digest(proxy_raw),
        "qualification_page": page_index + 1,
        "qualification_page_text_sha256": digest(page.encode()),
        "citation_sha256": digest(cite[0].encode()),
        "reporter_cik": reporter_cik,
        "reporter_legal_name_sha256": digest(reporter.encode()),
        "reporter_SEC_header_sha256": digest(header),
        "reporter_tax_identifier_verified": True,
        "cited_reported_filing_date": cited_day,
        "cited_ownership_event_date": event_day,
        "reported_subject_tax_identifiers": [v.decode() for v in subject_eins],
        "subject_tax_identifier_matches_FDIC_cover": subject_eins == [periodic["EIN"].encode()],
        "subject_tax_identifier_used_as_join_key": False,
        "qualification_basis": "FDIC_named_issuer_same_certificate_and_exact_reporter_address_filing_event_and_all_positions_citation",
    }
    own["available_by"] = max(
        instant(own["available_by"]), instant(proxy["available_by"])
    ).isoformat()
    own["availability_basis"] = "maximum_of_ownership_and_FDIC_issuer_proxy_evidence"
    result = join_identity(periodic, own)
    result["identity_basis"] = (
        "FDIC_periodic_and_exact_issuer_filed_SEC_ownership_citation_not_subject_tax_metadata"
    )
    return result
