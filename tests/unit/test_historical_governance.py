from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from edgar_moe.data.historical_governance import (
    governing_reference,
    governing_voting_evidence,
    qualify_ownership_voting,
)
from edgar_moe.data.historical_identity import IdentityEvidenceError
from scripts import capture_historical_security_identity as collector

CIK = "0000000001"
ACCESSION = "0000000001-18-000001"
TITLE = "Common Stock voting, $0.0025 par value"
CUTOFF = datetime(2022, 12, 28, tzinfo=UTC)
LINK = "https://www.sec.gov/Archives/edgar/data/1/000000000118000001/articles.htm"


def index():
    return f"""<table><tr><td>3.1</td><td><a href="{LINK}">Amended and Restated
Articles of Incorporation of Example Inc., reflecting all amendments thereto through
May 17, 2018, incorporated herein by reference from Exhibit 3.1e to our Form 10-Q
filed August 9, 2018.</a></td></tr></table>""".encode()


def charter():
    return b"""<html>ARTICLE I NAME The name of the Corporation is Example Inc.
ARTICLE II REGISTERED OFFICE ARTICLE III PURPOSE ARTICLE IV AUTHORIZED SHARES
The total number of shares of capital stock which the Corporation has authority to issue
is 900,000,000 shares of common stock, par value $0.0025 per share ("Common Stock").
The holders of the Common Stock shall be entitled to one (1) vote per share on all
matters to be voted on by the Corporation's shareholders. As and when dividends are
declared or paid, the holders of Common Stock shall be entitled to participate in such
dividends ratably on a per share basis. The holders of the Common Stock shall be entitled
to participate ratably on a per share basis in all distributions to the holders of the
Common Stock in any liquidation, dissolution or winding up of the Corporation.
ARTICLE V DIRECTORS ARTICLE VI OTHER PROVISIONS</html>"""


def submission():
    return (
        b"""<SEC-HEADER>
<ACCEPTANCE-DATETIME>20180809170527
ACCESSION NUMBER: 0000000001-18-000001
CONFORMED SUBMISSION TYPE: 10-Q
FILED AS OF DATE: 20180809
FILER:
    COMPANY DATA:
        COMPANY CONFORMED NAME: Example Inc.
        CENTRAL INDEX KEY: 0000000001
</SEC-HEADER>
<DOCUMENT>
<TYPE>EX-3.1(E)
<FILENAME>articles.htm
<TEXT>"""
        + charter()
        + b"</TEXT>\n</DOCUMENT>"
    )


def extract(body=None, **changes):
    return governing_voting_evidence(
        submission() if body is None else body,
        **{
            "cik": CIK,
            "accession": ACCESSION,
            "filename": "articles.htm",
            "reference": governing_reference(index()),
            "expected_title": TITLE,
            "cutoff": CUTOFF,
            **changes,
        },
    )


def test_exact_incorporated_single_class_charter_binds_issuer_dates_rights_and_source():
    reference = governing_reference(index())
    assert reference["href"] == LINK
    assert reference["amendments_through"] == "2018-05-17"
    result = extract()
    assert result["issuer_cik"] == CIK
    assert result["votes_per_share"] == 1
    assert result["authorized_common_shares"] == "900,000,000"
    assert result["available_by"] == "2018-08-23T00:00:00+00:00"
    assert result["reported_local_acceptance_datetime"] == "20180809170527"
    assert "accepted_at" not in result
    assert len(result["complete_submission_sha256"]) == 64
    assert len(result["authorized_article_sha256"]) == 64


def test_governing_reference_cannot_drop_another_amendment_or_borrow_a_link():
    with pytest.raises(IdentityEvidenceError, match="index_references"):
        governing_reference(index() + b"<table><tr><td>Articles of Amendment</td></tr></table>")
    with pytest.raises(IdentityEvidenceError, match="index_links"):
        governing_reference(
            index().replace(b"<a href=", b"<span data-source=").replace(b"</a>", b"</span>")
        )
    assert (
        governing_reference(index().replace(b"reflecting all amendments thereto", b"excerpt"))
        is None
    )


@pytest.mark.parametrize(
    "before,after",
    [
        (b"CENTRAL INDEX KEY: 0000000001", b"CENTRAL INDEX KEY: 0000000002"),
        (b"COMPANY CONFORMED NAME: Example Inc.", b"COMPANY CONFORMED NAME: Another Inc."),
        (b"CONFORMED SUBMISSION TYPE: 10-Q", b"CONFORMED SUBMISSION TYPE: 8-K"),
        (b"ACCESSION NUMBER: 0000000001-18-000001", b"ACCESSION NUMBER: 0000000001-18-000002"),
        (b"FILED AS OF DATE: 20180809", b"FILED AS OF DATE: 20180810"),
        (b"<ACCEPTANCE-DATETIME>20180809170527", b"<ACCEPTANCE-DATETIME>20180810170527"),
        (b"<TYPE>EX-3.1(E)", b"<TYPE>EX-3.2"),
        (b"<TYPE>EX-3.1(E)", b"<TYPE>EX-3.1((E))"),
        (b"<FILENAME>articles.htm", b"<FILENAME>other.htm"),
        (b"Corporation is Example Inc.", b"Corporation is Another Inc."),
        (b"one (1) vote", b"two (2) votes"),
        (b"$0.0025 per share", b"$0.025 per share"),
        (b"shares of common stock", b"shares of Class A common stock"),
        (b"ARTICLE V DIRECTORS", b"Preferred stock is also authorized. ARTICLE V DIRECTORS"),
        (
            b"ARTICLE V DIRECTORS",
            b"The Common Stock shall have no voting rights. ARTICLE V DIRECTORS",
        ),
        (b"ARTICLE V DIRECTORS", b"ARTICLE IV AMENDMENT ARTICLE V DIRECTORS"),
        (b"ARTICLE V DIRECTORS", b"ARTICLE V Common Stock shall have ten votes. DIRECTORS"),
        (b"ARTICLE V DIRECTORS", b"ARTICLE V Common Shares shall have ten votes. DIRECTORS"),
    ],
)
def test_issuer_date_filename_class_and_rights_contradictions_fail_closed(before, after):
    with pytest.raises(IdentityEvidenceError):
        extract(submission().replace(before, after))


def test_governing_source_must_be_available_and_cannot_qualify_another_security_class():
    with pytest.raises(IdentityEvidenceError):
        extract(cutoff=datetime(2018, 8, 22, tzinfo=UTC))
    for title in [
        "Class A Common Stock voting",
        "Non-voting Common Stock",
        "Ordinary Shares voting",
    ]:
        with pytest.raises(IdentityEvidenceError):
            extract(expected_title=title)


def test_qualification_preserves_original_title_and_requires_same_issuer_class_par_and_dates():
    periodic = {"issuer_cik": CIK, "security_title": TITLE}
    own = {
        "issuer_cik": CIK,
        "security_title": "Common Stock",
        "accepted_at": "2022-02-01T12:00:00Z",
    }
    evidence = extract()
    result = qualify_ownership_voting(periodic, own, evidence)
    assert result["security_title"] == "Voting Common Stock"
    assert result["reported_security_title"] == "Common Stock"
    assert own["security_title"] == "Common Stock"
    for changes in [
        {"issuer_cik": "0000000002"},
        {"security_title": "Class A Common Stock"},
        {"security_title": "Non-voting Common Stock"},
        {"security_title": "Common Stock, $0.025 par value"},
        {"accepted_at": "2018-08-22T00:00:00Z"},
    ]:
        with pytest.raises(IdentityEvidenceError):
            qualify_ownership_voting(periodic, {**own, **changes}, evidence)
    modified = deepcopy(periodic)
    modified["security_title"] = TITLE.replace("0.0025", "0.025")
    with pytest.raises(IdentityEvidenceError):
        qualify_ownership_voting(modified, own, evidence)


def test_governing_fallback_refuses_competing_identifiers_before_any_source_read():
    class Forbidden:
        def get(self, *args):
            pytest.fail("ambiguous candidate caused a source request")

    refusals = Counter()
    candidates = [({"cusip": value}, {}) for value in ["123456782", "234567890"]]
    assert (
        collector.governance_fallback(
            [], {"security_title": TITLE}, candidates, Forbidden(), Forbidden(), CUTOFF, refusals
        )
        is None
    )
    assert refusals["ambiguous_governing_candidate_identifiers"] == 1


@pytest.mark.parametrize("failure_leg", ["retained", "captured"])
def test_governing_fallback_propagates_source_integrity_failures(monkeypatch, failure_leg):
    class Retained:
        def filing(self, row):
            if failure_leg == "retained":
                raise IdentityEvidenceError("retained_source_digest_mismatch")
            return index()

    class Cache:
        def get(self, url):
            raise IdentityEvidenceError("captured_source_digest_mismatch")

    monkeypatch.setattr(
        collector,
        "periodic_anchor",
        lambda *args, **kwargs: {"security_title": TITLE, "available_by": "2022-11-08T00:00:00Z"},
    )
    row = {
        "cik": CIK,
        "form": "10-K",
        "accessionNumber": "0000000001-22-000001",
        "primaryDocument": "annual.htm",
        "acceptanceDateTime": "2022-11-01T00:00:00Z",
    }
    with pytest.raises(IdentityEvidenceError, match="source_digest_mismatch"):
        collector.governance_fallback(
            [row],
            {"security_title": TITLE, "issuer_cik": CIK, "symbol_at_cutoff": "OLD"},
            [({"cusip": "123456782"}, {})],
            Cache(),
            Retained(),
            CUTOFF,
            Counter(),
        )
