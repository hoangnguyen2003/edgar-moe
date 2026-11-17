from copy import deepcopy
from datetime import UTC, datetime
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from edgar_moe.data.historical_bank_filings import (
    decoder_identity,
    filing_metadata,
    join_bank_ownership,
    pdf_pages,
    periodic_report,
    qualify_bank_proxy,
    select_filing,
)
from edgar_moe.data.historical_identity import IdentityEvidenceError
from edgar_moe.data.screen_inputs import canonical_bytes

CUTOFF = datetime(2022, 12, 28, tzinfo=UTC)
CIK = "0000000001"
OWN = "0000000002-22-000001"
COVER = """FEDERAL DEPOSIT INSURANCE CORPORATION
FORM 10-Q
For the quarterly period ended: September 30, 2022
FDIC Certificate Number 12345
EXAMPLE BANK
(Exact name of registrant as specified in its charter)
12-3456789 (I.R.S. Employer Identification No.)
Securities registered pursuant to Section 12(b) of the Act:
Title of each class Trading Symbol(s) Name of each exchange on which registered
Common Stock, $0.01 par value per share SYN New York Stock Exchange
Depositary Shares of Preferred Stock SYN-P New York Stock Exchange
Indicate by check mark whether the registrant has filed all reports."""
TAIL = "SIGNATURES\nDate: November 8, 2022\nExample Bank"
PROXY_COVER = """FEDERAL DEPOSIT INSURANCE CORPORATION
SCHEDULE 14A
Example Bank
(Name of Registrant as Specified In Its Charter)"""
PROXY_PAGE = """Security Ownership of Certain Beneficial Owners
The following table sets forth the beneficial ownership of the common stock as of March 21, 2022.

Example Manager Group (1) 110 10.6%
100 Example Blvd.
Sample, PA 12345

(1) This information is derived from the Schedule 13G/A, filed by Example Manager Group on February 9, 2022, which states that Example Manager Group has sole voting power over no shares, shared voting power over 3 shares, sole dispositive power over 100 shares and shared dispositive power over 10 shares, as of December 31, 2021.
"""


def pdf(*pages):
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=1200, height=2000)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )

        def escape(s):
            return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

        content = (
            "BT /F1 8 Tf 20 1900 Td 12 TL "
            + " ".join("(" + escape(line) + ") Tj T*" for line in text.splitlines())
            + " ET"
        )
        stream = DecodedStreamObject()
        stream.set_data(content.encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def record(identifier=7, form="Form 10-Q~Quarterly Report", date="2022-11-08", size=100):
    return {
        "orgCertNum": 12345,
        "instFlngId": identifier,
        "instFincLglName": "Example Bank",
        "instFlngForm": {"instFlngFormNme": form, "instFlngFormConfInd": "N"},
        "instFlngCmpltCde": "S",
        "sysAddRecDttm": date,
        "sysLstActDttm": date,
        "instFlngEvtCausDttm": date,
        "instFlngAtchList": [
            {
                "instFlngId": identifier,
                "instFlngAtchId": 1,
                "instFlngAtchAmndCde": "O",
                "instFlngAtchStatCde": "S",
                "instFlngAtchSizeQty": size,
                "instFlngAtchOrglNme": "report.pdf",
                "instFlngAtchCrteDttm": date + "T15:00:00Z",
                "sysLstActDttm": date + "T15:01:00Z",
            }
        ],
    }


def periodic(cover=COVER, tail=TAIL):
    raw = pdf(cover, tail)
    row = record(size=len(raw))
    meta = filing_metadata(row, canonical_bytes(row), CUTOFF)
    return periodic_report(raw, meta, cik=CIK, symbol="SYN", cutoff=CUTOFF)


def ownership():
    body = (
        "Example Bank (Name of Issuer) Common Stock (Title of Class of Securities) "
        "123456782 (CUSIP Number) Date of Event Which Requires Filing of this Statement: December 31, 2021 "
        "1. NAME OF REPORTING PERSON I.R.S. IDENTIFICATION NO. OF ABOVE PERSON Example Manager Group - 12-3456789 2. CHECK BOX "
        "5. SOLE VOTING POWER 0 6. SHARED VOTING POWER 3 7. SOLE DISPOSITIVE POWER 100 8. SHARED DISPOSITIVE POWER 10 "
        "9. AGGREGATE AMOUNT BENEFICIALLY OWNED BY EACH REPORTING PERSON 110 10. CHECK BOX "
        "Item 2(b) - Address of Principal Business Office or, if none, residence: 100 Example Blvd. Sample PA 12345 Item 2(c) - Citizenship: Pennsylvania"
    )
    raw = (
        f"<SEC-HEADER><ACCEPTANCE-DATETIME>20220209160000\nACCESSION NUMBER:\t{OWN}\nCONFORMED SUBMISSION TYPE:\tSC 13G/A\nFILED AS OF DATE:\t20220209\n"
        "SUBJECT COMPANY:\n\tCOMPANY CONFORMED NAME:\tExample Bank\n\tCENTRAL INDEX KEY:\t0000000001\n\tIRS NUMBER:\t999999999\n"
        "FILED BY:\n\tCOMPANY CONFORMED NAME:\tExample Manager Holdings Inc.\n\tCENTRAL INDEX KEY:\t0000000002\n\tIRS NUMBER:\t123456789\n</SEC-HEADER>"
        f"<DOCUMENT>\n<TYPE>SC 13G/A\n<FILENAME>own.htm\n<TEXT>{body}</TEXT>\n</DOCUMENT>"
    ).encode()
    row = {
        "cik": CIK,
        "form": "SC 13G/A",
        "accessionNumber": OWN,
        "primaryDocument": "own.htm",
        "acceptanceDateTime": "2022-02-09T21:00:00Z",
    }
    return raw, row


def qualify(page=PROXY_PAGE, cover=PROXY_COVER, own_change=None):
    raw = pdf(cover, page)
    row = record(8, "Definitive Proxy/Information Statements", "2022-04-04", len(raw))
    meta = filing_metadata(row, canonical_bytes(row), CUTOFF)
    own, m = ownership()
    if own_change:
        own = own.replace(own_change[0].encode(), own_change[1].encode())
    return qualify_bank_proxy(periodic(), own, m, raw, meta, cutoff=CUTOFF)


def test_exact_issuer_citation_is_a_distinct_qualified_bridge_with_tax_conflict_preserved():
    raw, row = ownership()
    before = deepcopy(row)
    with pytest.raises(IdentityEvidenceError, match="name_or_EIN_conflict"):
        join_bank_ownership(periodic(), raw, row, cutoff=CUTOFF)
    result = qualify()
    proof = result["ownership_evidence"]["FDIC_issuer_citation_evidence"]
    assert result["security_id"] == "cusip:123456782" and row == before
    assert result["periodic_evidence"]["regulator"] == "FDIC"
    assert proof["reported_subject_tax_identifiers"] == ["999999999"]
    assert proof["subject_tax_identifier_matches_FDIC_cover"] is False
    assert proof["subject_tax_identifier_used_as_join_key"] is False
    assert proof["reporter_tax_identifier_verified"] is True
    assert proof["qualification_page"] == 2


def test_decoder_is_bound_and_original_and_preferred_rows_stay_distinct():
    result = periodic()
    assert len(decoder_identity()) == 64
    assert result["security_title"] == "Common Stock, $0.01 par value per share"
    assert result["accession"] == "FDIC:12345:7:1"
    assert result["available_by"] == "2022-11-23T00:00:00+00:00"


@pytest.mark.parametrize(
    "old,new",
    [
        ("FEDERAL DEPOSIT INSURANCE CORPORATION", "SOME OTHER REGULATOR"),
        ("FORM 10-Q", "FORM 10-K"),
        ("September 30, 2022", "December 31, 2022"),
        ("September 30, 2022", "September 30, 2021"),
        ("12-3456789", "00-0000000"),
        ("12-3456789", "12-3456789 98-7654321"),
        ("EXAMPLE BANK", "ANOTHER BANK"),
        ("Certificate Number 12345", "Certificate Number 12346"),
        (
            "Common Stock, $0.01 par value per share SYN",
            "Ordinary Shares, $0.01 par value per share SYN",
        ),
        (
            "Common Stock, $0.01 par value per share SYN",
            "Class B Common Stock, $0.01 par value per share SYN",
        ),
        (
            "Common Stock, $0.01 par value per share SYN",
            "Non-voting Common Stock, $0.01 par value per share SYN",
        ),
        ("per share SYN", "per share SYNX"),
        ("New York Stock Exchange", "Unverified Venue"),
        (
            "Common Stock, $0.01 par value per share SYN New York Stock Exchange",
            "Common Stock, $0.01 par value per share SYN New York Stock Exchange\nCommon Stock, $0.01 par value per share SYN New York Stock Exchange",
        ),
    ],
)
def test_conflicting_cover_registrations_dates_and_issuers_are_refused(old, new):
    assert old in COVER
    with pytest.raises(IdentityEvidenceError):
        periodic(COVER.replace(old, new))


@pytest.mark.parametrize(
    "tail",
    ["No signatures", "SIGNATURES\nDate: November 9, 2022", "SIGNATURES\nDate: November 32, 2022"],
)
def test_report_signatures_must_match_regulator_filing_date(tail):
    with pytest.raises(IdentityEvidenceError):
        periodic(tail=tail)


@pytest.mark.parametrize(
    "field,value",
    [
        ("orgCertNum", 99999),
        ("instFlngCmpltCde", "D"),
        (
            "instFlngForm",
            {"instFlngFormNme": "Form 10-Q~Quarterly Report", "instFlngFormConfInd": "Y"},
        ),
        ("sysLstActDttm", "2022-01-01"),
    ],
)
def test_index_detail_changes_and_unqualified_status_are_refused(field, value):
    selected = record()
    details = selected | {field: value}
    with pytest.raises(IdentityEvidenceError, match="index_detail_payload_mismatch"):
        filing_metadata(selected, canonical_bytes(details), CUTOFF)
    if field != "orgCertNum":
        with pytest.raises(IdentityEvidenceError):
            filing_metadata(details, canonical_bytes(details), CUTOFF)


def test_duplicate_filing_days_and_mixed_certificate_indices_cannot_select_arbitrarily():
    row = record()
    with pytest.raises(IdentityEvidenceError, match="ambiguous_latest"):
        select_filing(canonical_bytes([row, record(8)]), 12345, CUTOFF)
    with pytest.raises(IdentityEvidenceError, match="issuer_or_filing"):
        select_filing(canonical_bytes([row, row | {"orgCertNum": 99999}]), 12345, CUTOFF)
    assert select_filing(canonical_bytes([record(6, date="2022-08-08"), row]), 12345, CUTOFF) == row


def test_late_upload_or_record_change_does_not_inherit_an_old_filing_date():
    row = record()
    row["sysLstActDttm"] = "2022-12-20"
    row["instFlngEvtCausDttm"] = "2022-12-20"
    with pytest.raises(IdentityEvidenceError, match="not_available_by_cutoff"):
        filing_metadata(row, canonical_bytes(row), CUTOFF)


def test_later_paper_to_electronic_upload_uses_the_latest_original_source_timestamp():
    row = record(date="2022-04-04")
    row["sysLstActDttm"] = row["instFlngEvtCausDttm"] = "2022-04-07"
    row["instFlngAtchList"][0].update(
        instFlngAtchCrteDttm="2022-04-07T15:00:00Z", sysLstActDttm="2022-04-07T16:00:00Z"
    )
    assert (
        filing_metadata(row, canonical_bytes(row), CUTOFF)["available_by"]
        == "2022-04-22T00:00:00+00:00"
    )


@pytest.mark.parametrize(
    "old,new",
    [
        ("(1) 110", "(2) 110"),
        ("110 10.6%", "111 10.6%"),
        ("100 Example Blvd.", "101 Example Blvd."),
        ("February 9, 2022", "February 8, 2022"),
        ("December 31, 2021", "December 30, 2021"),
        ("over 3 shares", "over 4 shares"),
        ("over 100 shares", "over 101 shares"),
        ("over 10 shares", "over 11 shares"),
        ("sole voting power over no shares", "sole voting power over one share"),
        ("common stock as of", "ordinary shares as of"),
    ],
)
def test_changed_exact_citation_facts_fail_closed(old, new):
    assert old in PROXY_PAGE
    with pytest.raises(IdentityEvidenceError):
        qualify(PROXY_PAGE.replace(old, new))


@pytest.mark.parametrize(
    "old,new",
    [
        ("Common Stock (Title", "Class B Common Stock (Title"),
        ("GROUP - 12", "GROUP - 13"),
        ("IRS NUMBER:\t123456789", "IRS NUMBER:\t123456780"),
        ("POWER 100", "POWER 101"),
        ("KEY:\t0000000001", "KEY:\t0000000003"),
    ],
)
def test_other_subject_reporter_positions_or_classes_cannot_use_the_citation(old, new):
    # Case-sensitive source replacements must alter at least one byte.
    raw, _ = ownership()
    if old.encode() not in raw:
        old, new = old.title(), new.title()
    assert old.encode() in raw
    with pytest.raises(IdentityEvidenceError):
        qualify(own_change=(old, new))


def test_duplicate_ownership_pages_and_unsupported_pdf_bytes_refuse():
    with pytest.raises(IdentityEvidenceError):
        qualify(PROXY_PAGE + "\n" + PROXY_PAGE)
    for raw in [b"", b"not a PDF", b"%PDF-1.7 broken"]:
        with pytest.raises(IdentityEvidenceError):
            pdf_pages(raw)
