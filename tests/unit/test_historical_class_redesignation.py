from copy import deepcopy
from datetime import UTC, datetime

import pytest

from edgar_moe.data.historical_class_redesignation import (
    qualify_redesignated_class,
    redesignation_evidence,
    redesignation_reference,
)
from edgar_moe.data.historical_identity import IdentityEvidenceError, join_identity

CIK = "0000000001"
ACCESSION = "0000000001-12-000001"
URL = "https://www.sec.gov/Archives/edgar/data/1/000000000112000001/charter.htm"
CUTOFF = datetime(2022, 12, 28, tzinfo=UTC)
TITLE = "Common Stock, par value $.01 per share"
PRIMARY = (
    "The amendments became effective upon the filing of the Third Amended and Restated "
    "Certificate of Incorporation with the Secretary of State of the State of Delaware on May 2, 2012. "
    "The amendments rename the Company’s class A common stock as “common stock”."
)
CHARTER = (
    "THIRD AMENDED AND RESTATED CERTIFICATE OF INCORPORATION OF EXAMPLE INC. May 2, 2012 "
    "ARTICLE 1 NAME The name of the corporation (the Corporation) is: Example Inc. ARTICLE 2 "
    "The total number of shares of stock which the Corporation shall have authority to issue is 8,500, "
    "consisting of 7,500 shares of common stock, par value $.01 per share (the “Common Stock”) "
    "and 1,000 shares of preferred stock, par value $.01 per share (the “Preferred Stock”). "
    "Effective upon the filing of this Amended and Restated Certificate of Incorporation, "
    "without any further action on the part of the Corporation or its stockholders, "
    "(i) each share of the Corporation’s Class A common stock, par value $.01 "
    "per share (the “Class A Common Stock”) outstanding immediately prior thereto "
    "shall be redesignated as one share of Common Stock and (ii) all references to the Class A "
    "Common Stock or any right to purchase or acquire the Class A Common Stock "
    "(whether in the Certificate of Incorporation or otherwise) shall refer to the Common Stock. "
    "(b) Common Stock Each share of Common Stock outstanding on any record date shall be entitled "
    "to one vote. (c) Preferred Stock"
)


def document(kind, filename, text):
    return f"<DOCUMENT>\n<TYPE>{kind}\n<FILENAME>{filename}\n<TEXT>{text}</TEXT>\n</DOCUMENT>"


def submission(primary=PRIMARY, charter=CHARTER):
    return (
        "<SEC-HEADER><ACCEPTANCE-DATETIME>20120504160000\n"
        f"ACCESSION NUMBER:\t{ACCESSION}\nCONFORMED SUBMISSION TYPE:\t10-Q\n"
        "FILED AS OF DATE:\t20120504\nFILER:\n\tCOMPANY CONFORMED NAME:\tExample Inc.\n"
        f"\tCENTRAL INDEX KEY:\t{CIK}\n\tSEC FILE NUMBER:\t001-11111\n</SEC-HEADER>"
        + document("10-Q", "primary.htm", primary)
        + document("EX-3.1", "charter.htm", charter)
    ).encode()


def reference(index=None):
    if index is None:
        index = f'<table><tr><td>3.1</td><td><a href="{URL}">Third Amended and Restated Certificate of Incorporation</a></td><td>10-Q</td><td>001-11111</td><td>3.1</td><td>5/4/2012</td></tr></table>'.encode()
    return redesignation_reference(index, parent_url=URL, cik=CIK)


def evidence(raw=None, **kwargs):
    return redesignation_evidence(
        submission() if raw is None else raw,
        reference=reference(),
        primary_filename="primary.htm",
        cik=CIK,
        expected_title=TITLE,
        cutoff=CUTOFF,
        **kwargs,
    )


def anchors():
    periodic = dict(
        issuer_cik=CIK,
        security_title=TITLE,
        symbol_at_cutoff="SYN",
        exchange_at_filing="NYSE",
        accession="0000000001-22-000003",
        document_sha256="a" * 64,
        available_by="2022-11-08T00:00:00Z",
        accepted_at="2022-11-01T00:00:00Z",
        form="10-Q",
    )
    ownership = dict(
        issuer_cik=CIK,
        security_title="Class A Common Stock",
        cusip="123456782",
        accession="0000000002-22-000001",
        document_sha256="b" * 64,
        available_by="2022-02-23T00:00:00Z",
        accepted_at="2022-02-09T00:00:00Z",
        form="SC 13G/A",
    )
    return periodic, ownership


def test_explicit_charter_and_current_incorporation_join_without_mutating_reported_facts():
    proof = evidence()
    periodic, own = anchors()
    before = deepcopy(own)
    qualified = qualify_redesignated_class(periodic, own, proof)
    assert own == before
    assert proof["ratio"] == "1:1"
    assert proof["effective_date"] == "2012-05-02"
    assert proof["available_by"] == "2012-05-18T00:00:00+00:00"
    assert (
        qualified["charter_redesignation_evidence"]["reported_ownership_title"]
        == own["security_title"]
    )
    assert join_identity(periodic, qualified)["security_id"] == "cusip:123456782"


@pytest.mark.parametrize(
    "old,new",
    [
        ("CENTRAL INDEX KEY:\t0000000001", "CENTRAL INDEX KEY:\t0000000003"),
        ("COMPANY CONFORMED NAME:\tExample Inc.", "COMPANY CONFORMED NAME:\tAnother Inc."),
        ("SEC FILE NUMBER:\t001-11111", "SEC FILE NUMBER:\t001-11112"),
        ("FILED AS OF DATE:\t20120504", "FILED AS OF DATE:\t20120505"),
        ("CONFORMED SUBMISSION TYPE:\t10-Q", "CONFORMED SUBMISSION TYPE:\t10-K"),
        ("<TYPE>EX-3.1", "<TYPE>EX-3.2"),
        ("is: Example Inc.", "is: Another Inc."),
        ("8,500", "8,501"),
        ("7,500", "7,501"),
        ("par value $.01", "par value $.02"),
        ("redesignated as one share", "redesignated as two shares"),
        ("all references to the Class A", "some references to the Class A"),
        ("shall refer to the Common Stock", "may refer to the Common Stock"),
        ("one vote", "two votes"),
        ("(b) Common Stock", "(b) Non-voting Common Stock"),
        ("on May 2, 2012.", "on May 3, 2012."),
        ("OF EXAMPLE INC. May 2, 2012", "OF EXAMPLE INC. May 32, 2012"),
        ("rename the Company’s class A", "rename the Company’s class B"),
    ],
)
def test_conflicting_or_incomplete_legal_evidence_is_refused(old, new):
    raw = submission()
    assert old.encode() in raw
    with pytest.raises(IdentityEvidenceError):
        evidence(raw.replace(old.encode(), new.encode()))


@pytest.mark.parametrize("extra", [CHARTER, document("EX-3.1", "charter.htm", CHARTER)])
def test_duplicate_charter_provisions_or_documents_are_ambiguous(extra):
    raw = (
        submission(charter=CHARTER + " " + extra)
        if extra == CHARTER
        else submission() + extra.encode()
    )
    with pytest.raises(IdentityEvidenceError):
        evidence(raw)


@pytest.mark.parametrize(
    "title",
    [
        "Ordinary Shares",
        "Class B Common Stock",
        "Non-voting Common Stock",
        "Class A Common Stock, par value $.02",
    ],
)
def test_other_reported_families_classes_voting_and_par_are_not_overridden(title):
    periodic, own = anchors()
    with pytest.raises(IdentityEvidenceError):
        qualify_redesignated_class(periodic, own | {"security_title": title}, evidence())


@pytest.mark.parametrize(
    "change",
    [
        {"issuer_cik": "0000000003"},
        {"accepted_at": "2012-05-18T00:00:00Z"},
        {"accepted_at": "2011-01-01T00:00:00Z"},
    ],
)
def test_other_issuer_and_pre_evidence_ownership_are_refused(change):
    periodic, own = anchors()
    with pytest.raises(IdentityEvidenceError):
        qualify_redesignated_class(periodic, own | change, evidence())


def test_current_reference_is_unique_and_issuer_bounded():
    row = f'<tr>3.1 <a href="{URL}">Third Amended and Restated Certificate of Incorporation</a> 10-Q 001-11111 3.1 5/4/2012</tr>'
    for bad in [
        row + row,
        row.replace("/data/1/", "/data/2/"),
        row.replace("5/4/2012", "5/32/2012"),
        row.replace("3.1 5/4", "3.2 5/4"),
        row.replace("Third Amended", "Second Amended"),
    ]:
        with pytest.raises(IdentityEvidenceError):
            reference(("<table>" + bad + "</table>").encode())
