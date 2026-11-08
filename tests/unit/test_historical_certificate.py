from datetime import UTC, datetime
from hashlib import sha256

import pytest

from edgar_moe.data.historical_certificate import (
    certificate_location,
    certificate_reference,
    registered_certificate_identity,
)
from edgar_moe.data.historical_identity import IdentityEvidenceError

CIK = "0000000001"
ACCESSION = "0000000001-15-000001"
TITLE = "Class C Capital Stock, $0.001 par value"
CUTOFF = datetime(2022, 11, 1, tzinfo=UTC)
PARENT = "https://www.sec.gov/Archives/edgar/data/1/000000000122000001/annual.htm"
LINK = "http://www.sec.gov/Archives/edgar/data/1/000000000115000001/certificate.htm"


def index(href=LINK, title="Class C Capital Stock"):
    return f'<table><tr><td><a href="{href}">4.2</a></td><td>Specimen {title} certificate incorporated by reference.</td></tr></table>'.encode()


def certificate():
    return b"""<html><p>EXAMPLE INC. FULLY PAID AND NON-ASSESSABLE SHARES OF
CLASS C CAPITAL STOCK, PAR VALUE $0.001 PER SHARE, OF</p>
<p>CUSIP 123456 78 2</p></html>"""


def submission():
    return (
        b"""<SEC-HEADER>
<ACCEPTANCE-DATETIME>20151002161713
ACCESSION NUMBER: 0000000001-15-000001
CONFORMED SUBMISSION TYPE: 8-K12B
FILED AS OF DATE: 20151002
FILER:
    COMPANY DATA:
        COMPANY CONFORMED NAME: Example Inc.
        CENTRAL INDEX KEY: 0000000001
</SEC-HEADER>
<DOCUMENT>
<TYPE>EX-4.2
<FILENAME>certificate.htm
<TEXT>"""
        + certificate()
        + b"</TEXT>\n</DOCUMENT>"
    )


def extract(body=None, **changes):
    return registered_certificate_identity(
        submission() if body is None else body,
        **{
            "cik": CIK,
            "accession": ACCESSION,
            "filename": "certificate.htm",
            "expected_title": TITLE,
            "cutoff": CUTOFF,
            **changes,
        },
    )


def test_explicit_class_certificate_binds_original_filer_dates_filename_and_bytes():
    result = extract()
    assert result["issuer_cik"] == CIK
    assert result["cusip"] == "123456782"
    assert result["security_title"] == "CLASS C CAPITAL STOCK"
    assert result["available_by"] == "2015-10-16T00:00:00+00:00"
    assert result["reported_local_acceptance_datetime"] == "20151002161713"
    assert result["document_sha256"] == sha256(certificate()).hexdigest()
    assert result["complete_submission_sha256"] == sha256(submission()).hexdigest()
    assert "accepted_at" not in result  # No invented UTC acceptance timestamp.


def test_index_requires_explicit_matching_class_within_one_leaf_row():
    assert certificate_reference(index(), TITLE)["href"] == LINK
    assert certificate_reference(index(title="Class A Common Stock"), TITLE) is None
    split = b'<table><tr><td><a href="a.htm">4.2</a></td></tr><tr><td>Specimen Class C Capital Stock certificate</td></tr></table>'
    with pytest.raises(IdentityEvidenceError, match="index_links"):
        certificate_reference(split, TITLE)
    with pytest.raises(IdentityEvidenceError, match="index_references"):
        certificate_reference(index() + index("other.htm"), TITLE)


@pytest.mark.parametrize(
    "href",
    [
        "https://elsewhere.invalid/certificate.htm",
        "https://www.sec.gov@elsewhere.invalid/certificate.htm",
        LINK.replace("/data/1/", "/data/2/"),
        LINK + "?q=1",
        LINK + "#other",
        LINK.replace("certificate.htm", "image.jpg"),
    ],
)
def test_certificate_link_stays_on_exact_official_issuer_path(href):
    with pytest.raises(IdentityEvidenceError, match="source_link"):
        certificate_location(href, PARENT, CIK)


def test_only_transport_scheme_changes_for_an_explicit_historical_http_link():
    location = certificate_location(LINK, PARENT, CIK)
    assert location == {
        "url": LINK.replace("http:", "https:"),
        "accession": ACCESSION,
        "filename": "certificate.htm",
    }


@pytest.mark.parametrize(
    "old,new",
    [
        (b"CENTRAL INDEX KEY: 0000000001", b"CENTRAL INDEX KEY: 0000000002"),
        (b"ACCESSION NUMBER: 0000000001-15-000001", b"ACCESSION NUMBER: 0000000001-15-000002"),
        (b"CONFORMED SUBMISSION TYPE: 8-K12B", b"CONFORMED SUBMISSION TYPE: SC 13G"),
        (b"<TYPE>EX-4.2", b"<TYPE>EX-99.1"),
        (b"<FILENAME>certificate.htm", b"<FILENAME>other.htm"),
        (b"EXAMPLE INC. FULLY", b"OTHER INC. FULLY"),
        (b"CLASS C CAPITAL STOCK, PAR", b"CLASS A COMMON STOCK, PAR"),
        (b"CUSIP 123456 78 2", b"CUSIP 123456 78 0"),
        (b"CUSIP 123456 78 2", b"CUSIP 123456 78 2 CUSIP 037833100"),
        (b"FILED AS OF DATE: 20151002", b"FILED AS OF DATE: 20151302"),
        (b"FILED AS OF DATE: 20151002", b"FILED AS OF DATE: 20150902"),
    ],
)
def test_wrong_issuer_class_file_type_checksum_or_source_dates_cannot_supply_identity(old, new):
    with pytest.raises(IdentityEvidenceError):
        extract(submission().replace(old, new))


def test_late_ambiguous_or_redefined_certificate_evidence_is_refused():
    with pytest.raises(IdentityEvidenceError, match="available_by_index"):
        extract(cutoff=datetime(2015, 10, 15, tzinfo=UTC))
    doc = submission().split(b"<DOCUMENT>")[1]
    with pytest.raises(IdentityEvidenceError, match="ambiguous_certificate_exhibit"):
        extract(submission() + b"<DOCUMENT>" + doc)
    with pytest.raises(IdentityEvidenceError, match="exhibit_type"):
        extract(submission().replace(b"<TYPE>EX-4.2", b"<TYPE>EX-4.2\n<TYPE>EX-4.3"))
    with pytest.raises(IdentityEvidenceError, match="share_class_mismatch"):
        extract(expected_title="Class C Common Stock")


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "wrong_issuer",
        "SEC_source_cache_tampered",
        "source_request_budget_exhausted",
        "source_document_byte_bound",
        "prior_oversize",
    ],
)
def test_collector_follows_only_an_explicit_authenticated_class_certificate(monkeypatch, failure):
    from scripts import capture_historical_security_identity as collector

    annual = {
        "cik": CIK,
        "form": "10-K",
        "accessionNumber": "0000000001-22-000001",
        "acceptanceDateTime": "2022-02-01T12:00:00Z",
        "primaryDocument": "annual.htm",
    }
    quarter = {
        **annual,
        "form": "10-Q",
        "accessionNumber": "0000000001-22-000002",
        "acceptanceDateTime": "2022-09-01T12:00:00Z",
        "primaryDocument": "quarter.htm",
    }
    cover = (
        f'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="c">{CIK}</ix:nonNumeric>'
        '<ix:nonNumeric name="dei:TradingSymbol" contextRef="c">OLD</ix:nonNumeric>'
        f'<ix:nonNumeric name="dei:Security12bTitle" contextRef="c">{TITLE}</ix:nonNumeric>'
        '<ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c">Synthetic exchange</ix:nonNumeric>'
    ).encode()
    own = {**annual, "form": "SC 13G", "accessionNumber": "0000000001-22-000003"}
    monkeypatch.setattr(
        collector,
        "submissions",
        lambda *args: [quarter, annual, *([own] if failure == "prior_oversize" else [])],
    )
    calls = []

    class Retained:
        def filing(self, row):
            return cover + (index() if row == annual else b"")

    class Cache:
        def get(self, url):
            calls.append(url)
            if failure == "prior_oversize" and url.endswith(own["accessionNumber"] + ".txt"):
                raise IdentityEvidenceError("source_document_byte_bound")
            assert (
                url
                == "https://www.sec.gov/Archives/edgar/data/1/000000000115000001/0000000001-15-000001.txt"
            )
            if failure in {
                "SEC_source_cache_tampered",
                "source_request_budget_exhausted",
                "source_document_byte_bound",
            }:
                raise IdentityEvidenceError(failure)
            body = submission()
            if failure == "wrong_issuer":
                body = body.replace(
                    b"CENTRAL INDEX KEY: 0000000001", b"CENTRAL INDEX KEY: 0000000002"
                )
            return body

    def run():
        return collector.collect_member(
            {"cik": CIK, "symbol_at_snapshot": "OLD", "snapshot_member_key": "synthetic"},
            Cache(),
            Retained(),
            CUTOFF,
        )

    if failure in {"SEC_source_cache_tampered", "source_request_budget_exhausted"}:
        with pytest.raises(IdentityEvidenceError, match=failure):
            run()
    else:
        result = run()
        if failure in {None, "prior_oversize"}:
            assert result["status"] == "pre_cutoff_SEC_identity_anchored"
            anchor = result["anchor"]
            assert anchor["security_id"] == "cusip:123456782"
            assert "ownership_evidence" not in anchor
            if failure == "prior_oversize":
                assert result["source_size_refusals"] == {"source_document_byte_bound": 1}
            assert anchor["registration_certificate_evidence"]["issuer_cik"] == CIK
            assert (
                anchor["certificate_index_evidence"]["document_sha256"]
                == sha256(cover + index()).hexdigest()
            )
        else:
            assert result["status"] == (
                "source_size_unresolved"
                if failure == "source_document_byte_bound"
                else "ownership_identity_unresolved"
            )
    assert len(calls) == (2 if failure == "prior_oversize" else 1)
