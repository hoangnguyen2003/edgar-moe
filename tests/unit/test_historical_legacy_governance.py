from datetime import UTC, datetime

import pytest

from edgar_moe.data.historical_governance import qualify_ownership_voting
from edgar_moe.data.historical_identity import IdentityEvidenceError
from edgar_moe.data.historical_legacy_governance import legacy_reference, legacy_voting_evidence

CIK = "0000000001"
ACCESSION = "0000000001-99-000001"
TITLE = "Common Stock voting, $0.10 par value"
CUTOFF = datetime(2022, 12, 28, tzinfo=UTC)
LINK = f"http://www.sec.gov/Archives/edgar/data/1/{ACCESSION}.txt"


def index():
    return f'''<table><tr><td>3.1</td><td><a href="{LINK}">Amended and Restated Certificate
of Incorporation dated November 10, 1998 - incorporated by reference to Exhibit 3.1
to the Company's Registration Statement No. 333-111111 on Form S-4 filed with the
Commission on February 9, 1999</a></td></tr></table>'''.encode()


def charter():
    return b"""The certificate is amended by increasing the number of authorized shares of Common Stock of the Corporation to 1,000,000,000 shares.
FIRST: The name of the corporation is Example Inc. (hereinafter called the Corporation).
SECOND: Registered office. THIRD: Purpose.
FOURTH: The total number of shares which the Corporation shall have authority to issue is One Billion Three Hundred Thousand (1,000,300,000), consisting of Three Hundred Thousand (300,000) shares of Preferred Stock, of the par value of One Dollar ($1.00) per share (hereinafter called "Preferred Stock"), and One Billion (1,000,000,000) shares of Common Stock, of the par value of Ten Cents ($.10) per share (hereinafter called "Common Stock").
The Board of Directors is hereby authorized to issue the shares of the Preferred Stock in one or more series, with series-specific preferences and rights.
Subject to the payment of preferred distributions, common shareholders may receive dividends.
In the event of the liquidation common shareholders receive remaining assets.
Except as otherwise expressly provided in any resolution adopted by the Board of Directors granting voting rights to the holders of shares of any series of Preferred Stock and except as otherwise required by law the entire voting power of the Corporation shall be vested in the Common Stock, and each share of Common Stock shall have one vote for each share thereof held.
FIFTH: Directors. Signed this 10th day of November, 1998.
SIXTH: Arrangements for creditors or any class of them."""


def source():
    return (
        b"""<SEC-DOCUMENT>
<SEC-HEADER>synthetic.hdr.sgml
ACCESSION NUMBER: 0000000001-99-000001
CONFORMED SUBMISSION TYPE: S-4
PUBLIC DOCUMENT COUNT: 2
FILED AS OF DATE: 19990209
FILER:
    COMPANY DATA:
        COMPANY CONFORMED NAME: Example Inc.
        CENTRAL INDEX KEY: 0000000001
    FILING VALUES:
        SEC FILE NUMBER: 333-111111
</SEC-HEADER>
<DOCUMENT>
<TYPE>S-4
<SEQUENCE>1
<TEXT>Synthetic registration.</TEXT>
</DOCUMENT>
<DOCUMENT>
<TYPE>EX-3.1
<SEQUENCE>2
<TEXT>"""
        + charter()
        + b"""</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>"""
    )


def extract(payload=None, **changes):
    return legacy_voting_evidence(
        source() if payload is None else payload,
        **{
            "reference": legacy_reference(index(), cik=CIK),
            "expected_title": TITLE,
            "cutoff": CUTOFF,
            **changes,
        },
    )


def test_legacy_sgml_without_filenames_or_acceptance_time_has_date_only_bound_common_rights():
    ref = legacy_reference(index(), cik=CIK)
    assert ref["url"] == LINK.replace("http:", "https:")
    proof = extract()
    assert proof["issuer_cik"] == CIK and proof["votes_per_share"] == 1
    assert proof["authorized_preferred_shares"] == 300000
    assert proof["authorized_common_shares"] == 1000000000
    assert proof["available_by"] == "1999-02-23T00:00:00+00:00"
    assert "accepted_at" not in proof and "reported_local_acceptance_datetime" not in proof
    assert all(
        len(proof[key]) == 64
        for key in (
            "complete_submission_sha256",
            "SEC_header_sha256",
            "document_sha256",
            "authorized_article_sha256",
        )
    )


@pytest.mark.parametrize(
    "before,after",
    [
        (b"CENTRAL INDEX KEY: 0000000001", b"CENTRAL INDEX KEY: 0000000002"),
        (b"COMPANY CONFORMED NAME: Example Inc.", b"COMPANY CONFORMED NAME: Another Inc."),
        (b"corporation is Example Inc.", b"corporation is Another Inc."),
        (b"ACCESSION NUMBER: 0000000001-99-000001", b"ACCESSION NUMBER: 0000000001-99-000002"),
        (b"CONFORMED SUBMISSION TYPE: S-4", b"CONFORMED SUBMISSION TYPE: 10-K"),
        (b"SEC FILE NUMBER: 333-111111", b"SEC FILE NUMBER: 333-222222"),
        (b"FILED AS OF DATE: 19990209", b"FILED AS OF DATE: 19990210"),
        (b"PUBLIC DOCUMENT COUNT: 2", b"PUBLIC DOCUMENT COUNT: 1"),
        (b"<SEQUENCE>2", b"<SEQUENCE>1"),
        (b"<TYPE>EX-3.1", b"<TYPE>EX-3.2"),
        (b"<TYPE>EX-3.1", b"<TYPE>EX-3.1\n<FILENAME>charter.htm"),
        (b"<SEC-HEADER>", b"<SEC-HEADER>\n<ACCEPTANCE-DATETIME>19990209120000\n"),
        (b"</SEC-DOCUMENT>", b""),
        (b"(300,000)", b"(300,001)"),
        (b"(1,000,300,000)", b"(1,000,400,000)"),
        (b"(300,000)", b"(3,,00000)"),
        (b"Three Hundred Thousand (300,000)", b"Two Hundred Thousand (300,000)"),
        (b"one vote for each share", b"two votes for each share"),
        (b"10th day of November, 1998", b"11th day of November, 1998"),
        (b"FIFTH: Directors.", b"FIFTH: Class B is authorized. Directors."),
        (b"FIFTH: Directors.", b"FIFTH: Classes A and B are authorized. Directors."),
        (b"FIFTH: Directors.", b"FIFTH: Ordinary shares are authorized. Directors."),
        (b"FIFTH: Directors.", b"FIFTH: Common Shares carry ten votes. Directors."),
        (b"FIFTH: Directors.", b"FIFTH: Non-voting stock is authorized. Directors."),
        (b"FIFTH: Directors.", b"FOURTH: Amended rights. FIFTH: Directors."),
        (b"series-specific preferences and rights", b"Common Stock preferences and rights"),
        (b"series-specific preferences and rights", b"common shares preferences and rights"),
        (b"<TYPE>S-4\n<SEQUENCE>1", b"<TYPE>EX-4.2\n<SEQUENCE>1"),
        (b"FIFTH: Directors.", b"FIFTH: A second authority to issue stock applies. Directors."),
        (
            b"common shareholders may receive dividends",
            b"common shareholders vote twice on dividends",
        ),
    ],
)
def test_wrong_issuer_dates_legacy_schema_classes_and_rights_fail_closed(before, after):
    assert before in source()
    with pytest.raises(IdentityEvidenceError):
        extract(source().replace(before, after))


@pytest.mark.parametrize(
    "before,after",
    [
        (b"February 9, 1999", b"February 9, 2000"),
        (b"November 10, 1998", b"November 10, 2001"),
        (LINK.encode(), LINK.replace("www.sec.gov", "private.invalid").encode()),
        (LINK.encode(), (LINK + "?source=other").encode()),
        (LINK.encode(), LINK.replace("/data/1/", "/data/2/").encode()),
        (b"on Form S-4", b"on Form 10-K"),
    ],
)
def test_reference_must_be_unique_and_exactly_bind_the_legacy_sec_source(before, after):
    with pytest.raises(IdentityEvidenceError):
        legacy_reference(index().replace(before, after), cik=CIK)


def test_duplicate_charter_references_or_submission_exhibits_cannot_be_dropped():
    with pytest.raises(IdentityEvidenceError):
        legacy_reference(index() + index(), cik=CIK)
    with pytest.raises(IdentityEvidenceError):
        extract(source().replace(b"<TYPE>S-4\n<SEQUENCE>1", b"<TYPE>EX-3.1\n<SEQUENCE>1"))


def test_only_missing_common_voting_qualifier_can_be_supplied_and_preferred_stays_distinct():
    proof = extract()
    periodic = {"issuer_cik": CIK, "security_title": TITLE}
    ownership = {
        "issuer_cik": CIK,
        "security_title": "Common Stock, $0.10 par value",
        "accepted_at": "2022-02-01T00:00:00Z",
    }
    assert (
        qualify_ownership_voting(periodic, ownership, proof)["reported_security_title"]
        == ownership["security_title"]
    )
    for title in (
        "Preferred Stock, $1 par value",
        "Non-voting Common Stock",
        "Class B Common Stock",
        "Ordinary shares voting, $0.10 par value",
    ):
        with pytest.raises(IdentityEvidenceError):
            extract(expected_title=title)
        with pytest.raises(IdentityEvidenceError):
            qualify_ownership_voting(periodic, ownership | {"security_title": title}, proof)
    with pytest.raises(IdentityEvidenceError):
        qualify_ownership_voting(periodic, ownership | {"issuer_cik": "0000000002"}, proof)
    with pytest.raises(IdentityEvidenceError):
        extract(cutoff=datetime(1999, 2, 22, tzinfo=UTC))


def test_empty_punctuation_only_names_are_not_an_authenticated_issuer():
    with pytest.raises(IdentityEvidenceError):
        extract(source().replace(b"Example Inc.", b"..."))
