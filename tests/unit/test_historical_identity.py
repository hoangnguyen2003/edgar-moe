from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from edgar_moe.data.historical_identity import (
    IdentityEvidenceError,
    class_signature,
    join_identity,
    ownership_identity,
    ownership_subject,
    periodic_anchor,
    valid_cusip,
)

CIK = "0000000001"
CUTOFF = datetime(2022, 12, 28, 23, 23, 50, tzinfo=UTC)
CUSIP = "123456782"  # Synthetic, with a valid check digit.


def metadata(form="10-K", **changes):
    return {
        "cik": CIK,
        "form": form,
        "accessionNumber": "0000000001-22-000001",
        "acceptanceDateTime": "2022-11-01T12:00:00Z",
        **changes,
    }


def fact(name, value, context="common"):
    return f'<ix:nonNumeric name="dei:{name}" contextRef="{context}">{value}</ix:nonNumeric>'


def periodic(symbol="OLD", title="Class A Common Stock, $0.01 par value", issuer="1"):
    return (
        "<html>"
        + fact("EntityCentralIndexKey", issuer, "issuer")
        + fact("TradingSymbol", symbol)
        + fact("Security12bTitle", title)
        + fact("SecurityExchangeName", "Synthetic exchange")
        + "</html>"
    ).encode()


def ownership(cusip=CUSIP, title="Class A Common Stock"):
    return (
        f"<html><p>Synthetic issuer</p><p>(Name of Issuer)</p><p>{title}</p>"
        f"<p>(Title of Class of Securities)</p><p>{cusip}</p><p>(CUSIP Number)</p></html>"
    ).encode()


def anchors():
    return (
        periodic_anchor(periodic(), metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF),
        ownership_identity(ownership(), metadata("SC 13G/A"), cik=CIK, cutoff=CUTOFF),
    )


def test_binds_issuer_symbol_class_and_cusip_from_two_dated_sources():
    p, o = anchors()
    joined = join_identity(p, o)
    assert joined["security_id"] == "cusip:" + CUSIP
    assert joined["symbol_at_cutoff"] == "OLD"
    assert joined["issuer_cik"] == CIK
    assert joined["current_asset_directory_used_for_selection"] is False
    assert joined["available_by"] == "2022-11-08T12:00:00+00:00"
    assert len(joined["periodic_evidence"]["document_sha256"]) == 64
    assert len(joined["ownership_evidence"]["document_sha256"]) == 64


def test_ticker_reuse_cannot_join_another_issuer_or_another_class():
    p, o = anchors()
    other = deepcopy(o)
    other["issuer_cik"] = "0000000002"
    with pytest.raises(IdentityEvidenceError, match="issuer"):
        join_identity(p, other)
    other = deepcopy(o)
    other["security_title"] = "Class B Common Stock"
    with pytest.raises(IdentityEvidenceError, match="class"):
        join_identity(p, other)


def test_later_rename_or_delisting_does_not_change_the_historical_anchor():
    p, o = anchors()
    first = join_identity(p, o)
    # Such current-state fields are deliberately not read by the join.
    p.update(current_symbol="NEW", active=False, current_asset_uuid="unrelated")
    second = join_identity(p, o)
    assert second["security_id"] == first["security_id"]
    assert second["symbol_at_cutoff"] == "OLD"
    assert "current_symbol" not in second


@pytest.mark.parametrize(
    "field,value",
    [
        ("cik", "0000000002"),
        ("form", "10-K/A"),
        ("accessionNumber", "not-an-accession"),
        ("acceptanceDateTime", "2023-01-01T00:00:00Z"),
        ("acceptanceDateTime", "2022-12-25T00:00:00Z"),
        ("acceptanceDateTime", "2022-11-01T12:00:00"),
    ],
)
def test_refuses_wrong_filing_identity_late_evidence_and_lagged_publication(field, value):
    with pytest.raises(IdentityEvidenceError):
        periodic_anchor(
            periodic(), metadata(**{field: value}), cik=CIK, symbol="OLD", cutoff=CUTOFF
        )


def test_refuses_document_issuer_even_if_requested_metadata_matches():
    with pytest.raises(IdentityEvidenceError, match="issuer"):
        periodic_anchor(periodic(issuer="2"), metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)


def test_never_joins_facts_across_different_contexts():
    body = periodic().replace(
        b'contextRef="common">Synthetic exchange', b'contextRef="other">Synthetic exchange'
    )
    with pytest.raises(IdentityEvidenceError, match="class"):
        periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)


def test_combined_registrants_bind_the_symbol_to_its_own_context_issuer():
    context = b'<xbrli:context id="common"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier></xbrli:entity></xbrli:context>'
    body = periodic() + fact("EntityCentralIndexKey", "2", "other-issuer").encode() + context
    assert (
        periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)["issuer_cik"] == CIK
    )
    with pytest.raises(IdentityEvidenceError, match="context_issuer"):
        periodic_anchor(
            body.replace(b">1</xbrli:identifier>", b">2</xbrli:identifier>"),
            metadata(),
            cik=CIK,
            symbol="OLD",
            cutoff=CUTOFF,
        )


def test_conflicting_registered_class_facts_are_not_arbitrarily_selected():
    body = periodic() + fact("Security12bTitle", "Class B Common Stock").encode()
    with pytest.raises(IdentityEvidenceError, match="class"):
        periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)


def test_different_valid_class_contexts_for_the_same_symbol_are_ambiguous():
    body = (
        periodic()
        + (
            fact("TradingSymbol", "OLD", "other")
            + fact("Security12bTitle", "Class B Common Stock", "other")
            + fact("SecurityExchangeName", "Synthetic exchange", "other")
        ).encode()
    )
    with pytest.raises(IdentityEvidenceError, match="context"):
        periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)


@pytest.mark.parametrize(
    "title",
    [
        "Preferred Stock",
        "Depositary Shares",
        "Senior Notes",
        "Series A Units",
        "Class A and Class B Common Stock",
        "unknown",
    ],
)
def test_unsupported_or_ambiguous_security_classes_fail_closed(title):
    with pytest.raises(IdentityEvidenceError):
        class_signature(title)


def test_class_equivalence_preserves_class_rights_and_distinguishes_common_from_ordinary():
    assert class_signature("Class A Common Stock, par value $0.01") == class_signature(
        "class A common stock"
    )
    assert class_signature("Class A Common Stock") != class_signature("Class B Common Stock")
    assert class_signature("Voting Common Stock") != class_signature("Non-voting Common Stock")
    assert class_signature("Common Stock") != class_signature("Ordinary Shares")


@pytest.mark.parametrize("value", ["123456783", "12345678", "1234567820", "12345678X", "abcdefgh3"])
def test_invalid_cusips_are_rejected(value):
    assert valid_cusip(value) is False
    with pytest.raises(IdentityEvidenceError, match="cusip"):
        ownership_identity(ownership(value), metadata("SC 13G"), cik=CIK, cutoff=CUTOFF)


def test_spaced_cusip_is_accepted_only_in_its_labeled_cover_field():
    result = ownership_identity(
        ownership("123456 78 2"), metadata("SC 13G"), cik=CIK, cutoff=CUTOFF
    )
    assert result["cusip"] == CUSIP


def test_unlabeled_or_multiple_cusips_cannot_supply_a_security_identity():
    with pytest.raises(IdentityEvidenceError):
        ownership_identity(b"<p>123456782</p>", metadata("SC 13G"), cik=CIK, cutoff=CUTOFF)
    # Another synthetic check-digit-valid identifier next to a CUSIP label.
    other = "234567899"
    assert valid_cusip(other)
    body = ownership() + f"<p>CUSIP No. {other}</p>".encode()
    with pytest.raises(IdentityEvidenceError, match="ambiguous_cusip"):
        ownership_identity(body, metadata("SC 13G"), cik=CIK, cutoff=CUTOFF)


def test_rejects_missing_class_and_post_cutoff_ownership_filings():
    with pytest.raises(IdentityEvidenceError, match="class"):
        ownership_identity(b"CUSIP No. 123456782", metadata("SC 13G"), cik=CIK, cutoff=CUTOFF)
    with pytest.raises(IdentityEvidenceError, match="cutoff"):
        ownership_identity(
            ownership(),
            metadata("SC 13G", acceptanceDateTime="2023-01-01T00:00:00Z"),
            cik=CIK,
            cutoff=CUTOFF,
        )


def submission_header(subject=CIK, owner="0000000002", accession="0000000001-22-000001"):
    return (
        f"<SEC-HEADER>\nACCESSION NUMBER: {accession}\nSUBJECT COMPANY:\n\tCOMPANY DATA:\n\t\tCENTRAL INDEX KEY: {subject}\nFILED BY:\n\tCOMPANY DATA:\n\t\tCENTRAL INDEX KEY: {owner}\n</SEC-HEADER>\n"
    ).encode()


def test_ownership_identity_binds_subject_not_reporting_owner():
    result = ownership_subject(
        submission_header() + ownership(), cik=CIK, accession=metadata()["accessionNumber"]
    )
    assert result["subject_cik"] == CIK
    with pytest.raises(IdentityEvidenceError, match="subject_issuer"):
        ownership_subject(
            submission_header(subject="0000000002", owner=CIK) + ownership(),
            cik=CIK,
            accession=metadata()["accessionNumber"],
        )


def test_bare_primary_document_cannot_prove_subject_issuer():
    with pytest.raises(IdentityEvidenceError, match="subject_header"):
        ownership_subject(ownership(), cik=CIK, accession=metadata()["accessionNumber"])


def test_subject_header_rejects_other_accession_and_multiple_subjects():
    with pytest.raises(IdentityEvidenceError, match="accession"):
        ownership_subject(
            submission_header(accession="0000000001-22-000002"),
            cik=CIK,
            accession=metadata()["accessionNumber"],
        )
    body = submission_header().replace(
        b"FILED BY:", b"SUBJECT COMPANY:\n\tCENTRAL INDEX KEY: 0000000003\nFILED BY:"
    )
    with pytest.raises(IdentityEvidenceError, match="subject_company"):
        ownership_subject(body, cik=CIK, accession=metadata()["accessionNumber"])
