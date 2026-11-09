from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from edgar_moe.data.historical_certificate import certificate_location
from edgar_moe.data.historical_class_charter import (
    class_charter_references,
    class_charter_voting_evidence,
    qualify_class_charter_ownership,
)
from edgar_moe.data.historical_identity import IdentityEvidenceError
from scripts import capture_historical_security_identity as collector

CIK = "0000000001"
TITLE = "Class B Common Stock (nonvoting), $0.15 par value"
BASE = "0000000001-12-000001"
AMENDMENT = "0000000001-16-000001"
PARENT = "https://www.sec.gov/Archives/edgar/data/1/000000000122000001/annual.htm"
CUTOFF = datetime(2022, 12, 28, tzinfo=UTC)


def index():
    return b"""<table><tr><td>3.1</td><td><a href="http://www.sec.gov/Archives/edgar/data/1/000000000112000001/base.htm">
Restated Certificate of Incorporation of registrant, incorporated into this report by reference
to Exhibit 3 ( i) of Example Corporation's Form 10-Q for the quarter ended July 31, 2012,
filed on September 5, 2012 (File No. 002-00001).</a></td></tr>
<tr><td>3.2</td><td><a href="http://www.sec.gov/Archives/edgar/data/1/000000000116000001/amend.htm">
Certificate of Amendment of Restated Certificate of Incorporation of registrant, incorporated
into this report by reference to Exhibit 3.1 of Example Corporation's Form 8-K filed on
August 9, 2016 (File No. 001-00001).</a></td></tr></table>"""


def authorization(*, amended=False):
    total, a = (
        ("Seven Hundred Million (700,000,000)", "Three Hundred Million (300,000,000)")
        if amended
        else ("Five Hundred Million (500,000,000)", "One Hundred Million (100,000,000)")
    )
    par = (
        "par value of Fifteen Cents ($0.15) per share"
        if amended
        else ("the par value of Fifteen Cents (15¢) each")
    )
    return (
        "The total number of shares of all classes of stock which the Corporation shall "
        f"have authority to issue is {total} shares, divided into (a) {a} shares of Class A "
        f"Common Stock of {par}; and (b) Four Hundred Million (400,000,000) shares of "
        f"Class B Common Stock of {par}."
    )


def charter():
    return (
        "FIRST : The name of this Corporation is EXAMPLE CORPORATION. SECOND : Office. "
        "THIRD : Purpose. FOURTH : " + authorization() + " "
        "Authorized but unissued shares of Class A Common Stock and of Class B Common Stock "
        "may be issued and sold from time to time by the Corporation for such consideration "
        "and upon such terms as may from time to time be fixed by the Board of Directors, "
        "without action by the stockholders. Rights of Class A Common Stock and Class B Common Stock . "
        "Every share of the common stock of both classes, whenever and for whatever "
        "consideration issued, shall be entitled to the same rights as every other share "
        "of common stock in all distributions of earnings or assets of the Corporation "
        "distributable to the holders of the common stock. Except as herein provided, "
        "the holders of the Class A Common Stock shall have full and exclusive voting "
        "powers. The Class B Common Stock shall be in all respects equal and identical "
        "to the Class A Common Stock except that the holders of the Class B Common "
        "Stock shall have no voting powers in the election of directors, or on any "
        "question, except as otherwise provided by the laws of Delaware. General Provisions . "
        "The Board of Directors may issue stock. FIFTH : Perpetual existence."
    )


def amendment():
    return (
        "Exhibit CERTIFICATE OF AMENDMENT TO THE RESTATED CERTIFICATE OF INCORPORATION OF "
        "EXAMPLE CORPORATION Pursuant to Section 242 of the General Corporation Law of the "
        "State of Delaware The undersigned authorized officer of Example Corporation, a "
        "corporation duly organized and existing under the General Corporation Law of the "
        "State of Delaware (the “Corporation”), hereby certifies that an amendment to the "
        "Corporation’s Restated Certificate of Incorporation has been duly adopted in accordance "
        "with the provisions of Section 242 of the General Corporation Law of the State of "
        "Delaware, as follows: The Restated Certificate of Incorporation of the Corporation is "
        "hereby amended by deleting the first paragraph of Article FOURTH thereof and inserting "
        "the following in lieu thereof: “FOURTH: " + authorization(amended=True) + "” "
        "The amendment set forth above was adopted in accordance with Section 242 of the "
        "General Corporation Law of the State of Delaware. This Certificate of Amendment "
        "shall not become effective until 4:00 p.m. Eastern Daylight Time on the date on which "
        "it is filed with the Delaware Secretary of State. [Signature Page Follows] "
        "IN WITNESS WHEREOF, the Corporation has caused this Certificate of Amendment to be "
        "executed by its duly authorized officer on this 8th day of August, 2016. "
        "EXAMPLE CORPORATION By: /s/ Jane Smith Title: Secretary Name: Jane Smith"
    )


def submission(text, *, amended=False):
    accession, date, form, filename = (
        (AMENDMENT, "20160809", "8-K", "amend.htm")
        if amended
        else (BASE, "20120905", "10-Q", "base.htm")
    )
    return f"""<SEC-HEADER>header\n<ACCEPTANCE-DATETIME>{date}161000
ACCESSION NUMBER: {accession}
CONFORMED SUBMISSION TYPE: {form}
FILED AS OF DATE: {date}
FILER:
    COMPANY DATA:
        COMPANY CONFORMED NAME: EXAMPLE CORP
        CENTRAL INDEX KEY: {CIK}
</SEC-HEADER>
<DOCUMENT>
<TYPE>EX-3.1
<FILENAME>{filename}
<TEXT><html>{text}</html></TEXT>
</DOCUMENT>""".encode()


def references(body=None):
    refs = class_charter_references(index() if body is None else body)
    return [{**r, **certificate_location(r["href"], PARENT, CIK)} for r in refs]


def extract(sources=None, **changes):
    return class_charter_voting_evidence(
        [submission(charter()), submission(amendment(), amended=True)]
        if sources is None
        else sources,
        **{
            "references": references(),
            "cik": CIK,
            "expected_title": TITLE,
            "cutoff": CUTOFF,
            **changes,
        },
    )


def test_named_class_and_nonvoting_rights_survive_share_count_only_amendment():
    proof = extract()
    assert proof["amended_authorized_share_counts"] == [700000000, 300000000, 400000000]
    assert proof["available_by"] == "2016-08-23T00:00:00+00:00"
    assert proof["statutory_voting_exceptions_preserved"] is True
    assert proof["amendment_executed_date"] == "2016-08-08"
    assert all("accepted_at" not in source for source in proof["sources"])
    assert len({source["complete_submission_sha256"] for source in proof["sources"]}) == 2


def test_split_same_href_is_allowed_but_another_link_or_unread_amendment_is_not():
    body = index().replace(
        b"Restated Certificate",
        b'Restated </a><a href="http://www.sec.gov/Archives/edgar/data/1/000000000112000001/base.htm">Certificate',
        1,
    )
    assert len(references(body)) == 2
    with pytest.raises(IdentityEvidenceError, match="index_links"):
        references(body.replace(b'/base.htm">Certificate', b'/other.htm">Certificate'))
    assert (
        class_charter_references(
            index() + b"<table><tr><td>Articles of Amendment</td></tr></table>"
        )
        is None
    )
    assert class_charter_references(index().replace(b"3.2", b"3.3")) is None


@pytest.mark.parametrize(
    "which,before,after",
    [
        (0, "CENTRAL INDEX KEY: 0000000001", "CENTRAL INDEX KEY: 0000000002"),
        (1, "ACCESSION NUMBER: " + AMENDMENT, "ACCESSION NUMBER: " + BASE),
        (1, "CONFORMED SUBMISSION TYPE: 8-K", "CONFORMED SUBMISSION TYPE: 10-Q"),
        (1, "EXAMPLE CORP\n", "OTHER CORP\n"),
        (1, "20160809161000", "20160810161000"),
        (1, "<TYPE>EX-3.1", "<TYPE>EX-3.2"),
        (1, "<FILENAME>amend.htm", "<FILENAME>other.htm"),
        (0, "no voting powers", "full voting powers"),
        (0, "Class B Common Stock shall be", "Class C Common Stock shall be"),
        (0, "The Board of Directors may issue stock.", "The Common Shares shall have ten votes."),
        (
            0,
            "FIFTH : Perpetual existence.",
            "FIFTH : Class B Common Stock shall have voting rights.",
        ),
        (1, "first paragraph of Article FOURTH", "entire Article FOURTH"),
        (1, "700,000,000", "701,000,000"),
        (1, "($0.15)", "($0.25)"),
        (1, "[Signature Page Follows]", "Additional rights are granted. [Signature Page Follows]"),
        (1, "8th day of August, 2016", "10th day of August, 2016"),
        (1, "EXAMPLE CORPORATION By", "OTHER CORPORATION By"),
    ],
)
def test_wrong_issuer_class_rights_dates_exhibit_or_amendment_scope_are_refused(
    which, before, after
):
    sources = [submission(charter()), submission(amendment(), amended=True)]
    assert before.encode() in sources[which]
    sources[which] = sources[which].replace(before.encode(), after.encode())
    with pytest.raises(IdentityEvidenceError):
        extract(sources)


def test_missing_late_reordered_or_other_registered_class_chain_is_refused():
    sources = [submission(charter()), submission(amendment(), amended=True)]
    for bad in [sources[:1], sources[::-1], [sources[0], sources[1] + sources[1]]]:
        with pytest.raises(IdentityEvidenceError):
            extract(bad)
    with pytest.raises(IdentityEvidenceError):
        extract(cutoff=datetime(2016, 8, 22, tzinfo=UTC))
    for title in [
        "Class A Common Stock voting, $0.15",
        "Common Stock nonvoting, $0.15",
        "Class B Ordinary Shares nonvoting, $0.15",
    ]:
        with pytest.raises(IdentityEvidenceError):
            extract(expected_title=title)


def test_ownership_still_needs_explicit_same_class_issuer_par_and_available_source():
    periodic = {"issuer_cik": CIK, "security_title": TITLE}
    own = {
        "issuer_cik": CIK,
        "security_title": "Class B Common Stock",
        "accepted_at": "2022-02-01T12:00:00Z",
    }
    proof = extract()
    result = qualify_class_charter_ownership(periodic, own, proof)
    assert result["security_title"] == "Non-voting Class B Common Stock"
    assert own["security_title"] == "Class B Common Stock"
    for changes in [
        {"issuer_cik": "0000000002"},
        {"security_title": "Common Stock"},
        {"security_title": "Class B Common Stock voting"},
        {"security_title": "Class B Common Stock, $0.25"},
        {"accepted_at": "2016-08-22T00:00:00Z"},
    ]:
        with pytest.raises(IdentityEvidenceError):
            qualify_class_charter_ownership(periodic, {**own, **changes}, proof)


def test_collector_refuses_identifier_ambiguity_before_any_source_read():
    periodic = {"issuer_cik": CIK, "security_title": TITLE}
    candidates = [
        ({"cusip": value, "security_title": "Class B Common Stock"}, {})
        for value in ["000000018", "000000026"]
    ]
    refusals = Counter()
    assert (
        collector.class_charter_fallback([], periodic, candidates, None, None, CUTOFF, refusals)
        is None
    )
    assert refusals["ambiguous_class_charter_candidate_identifiers"] == 1
    snapshot = deepcopy(candidates)
    assert candidates == snapshot


def test_collector_preserves_sources_and_owned_class_in_complete_chain(monkeypatch):
    periodic = {
        "issuer_cik": CIK,
        "security_title": TITLE,
        "symbol_at_cutoff": "EX.B",
        "exchange_at_filing": "Example Exchange",
        "available_by": "2022-11-08T00:00:00Z",
    }
    own = {
        "issuer_cik": CIK,
        "security_title": "Class B Common Stock",
        "cusip": "000000018",
        "accepted_at": "2022-02-01T12:00:00Z",
        "available_by": "2022-02-08T12:00:00Z",
        "accession": "0000000002-22-000001",
        "document_sha256": "a" * 64,
    }
    row = {
        "cik": CIK,
        "form": "10-K",
        "accessionNumber": "0000000001-22-000001",
        "primaryDocument": "annual.htm",
        "acceptanceDateTime": "2022-11-01T00:00:00Z",
    }
    calls = []

    class Retained:
        def filing(self, _row):
            return index()

    class Cache:
        def get(self, url):
            calls.append(url)
            if BASE in url:
                return submission(charter())
            assert AMENDMENT in url
            return submission(amendment(), amended=True)

    monkeypatch.setattr(collector, "periodic_anchor", lambda *a, **k: dict(periodic))
    result = collector.class_charter_fallback(
        [row], periodic, [(own, {"subject_cik": CIK})], Cache(), Retained(), CUTOFF, Counter()
    )
    assert result["security_id"] == "cusip:000000018"
    assert result["ownership_evidence"]["reported_security_title"] == own["security_title"]
    assert len(result["class_charter_evidence"]["sources"]) == 2
    assert len(calls) == 2
    assert result["ownership_subject_evidence"] == {"subject_cik": CIK}
    assert collector.code_identity()["class_charter_algorithm_sha256"]

    class Corrupt:
        def get(self, _url):
            raise IdentityEvidenceError("captured_source_digest_mismatch")

    with pytest.raises(IdentityEvidenceError, match="source_digest_mismatch"):
        collector.class_charter_fallback(
            [row], periodic, [(own, {})], Corrupt(), Retained(), CUTOFF, Counter()
        )
    refusals = Counter({"source_document_byte_bound": 1})
    assert (
        collector.class_charter_fallback(
            [row], periodic, [(own, {})], Corrupt(), Retained(), CUTOFF, refusals
        )
        is None
    )
