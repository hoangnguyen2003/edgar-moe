from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from edgar_moe.data.historical_identity import (
    MAX_RETAINED_DOCUMENT_BYTES,
    IdentityEvidenceError,
    RegistrationCoverExhibit,
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


def cover_row(symbol="OLD", title="Class A Common Stock", context="common"):
    return (
        "<tr><td>"
        + fact("Security12bTitle", title, context)
        + "</td><td>"
        + fact("TradingSymbol", symbol, context)
        + "</td><td>"
        + fact("SecurityExchangeName", "Synthetic exchange", context)
        + "</td></tr>"
    )


def test_same_context_multiple_securities_are_bound_by_an_explicit_cover_row():
    body = (
        fact("EntityCentralIndexKey", CIK, "issuer")
        + "<table>"
        + cover_row()
        + cover_row(symbol="BOND", title="Senior Notes")
        + "</table>"
    ).encode()
    result = periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)
    assert result["security_title"] == "Class A Common Stock"
    assert (
        result["registered_class_binding"]
        == "single_security_row_with_compatible_issuer_period_dimensions"
    )


def test_cover_rows_do_not_override_class_ambiguity_or_cross_context_facts():
    issuer = fact("EntityCentralIndexKey", CIK, "issuer")
    for rows in (
        cover_row() + cover_row(title="Class B Common Stock"),
        cover_row().replace('contextRef="common">Class A', 'contextRef="other">Class A')
        + cover_row(symbol="BOND", title="Senior Notes"),
    ):
        with pytest.raises(IdentityEvidenceError, match="class_context"):
            periodic_anchor(
                (issuer + "<table>" + rows + "</table>").encode(),
                metadata(),
                cik=CIK,
                symbol="OLD",
                cutoff=CUTOFF,
            )


def test_outer_layout_row_cannot_join_facts_from_nested_rows():
    body = (
        fact("EntityCentralIndexKey", CIK, "issuer")
        + "<table><tr><td>"
        + fact("TradingSymbol", "OLD")
        + "</td><td><table><tr><td>"
        + fact("Security12bTitle", "Class A Common Stock")
        + fact("SecurityExchangeName", "Synthetic exchange")
        + "</td></tr></table></td></tr></table>"
    ).encode()
    body += fact("Security12bTitle", "Class B Common Stock").encode()
    with pytest.raises(IdentityEvidenceError, match="class_context"):
        periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)


def colon_cover(title="Class A Common Stock", cusip=CUSIP):
    return f"<p>Name of issuer: Synthetic issuer</p><p>Title of Class of Securities: {title}</p><p>CUSIP Number: {cusip}</p>".encode()


def dimension_context(identifier, axis, member, day="2022-11-01"):
    return f'<xbrli:context id="{identifier}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="{axis}">{member}</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>{day}</xbrli:instant></xbrli:period></xbrli:context>'


def dimensional_cover(
    symbol_axis="dei:TradingSymbolAxis",
    symbol_member="synthetic:SymbolMember",
    exchange_day="2022-11-01",
):
    return (
        fact("EntityCentralIndexKey", CIK, "issuer")
        + dimension_context("title", "us-gaap:StatementClassOfStockAxis", "synthetic:ClassAMember")
        + dimension_context("symbol", symbol_axis, symbol_member)
        + dimension_context(
            "exchange", "dei:EntityListingsExchangeAxis", "synthetic:ExchangeMember", exchange_day
        )
        + "<table><tr><td>"
        + fact("Security12bTitle", "Class A Common Stock", "title")
        + "</td><td>"
        + fact("TradingSymbol", "OLD", "symbol")
        + "</td><td>"
        + fact("SecurityExchangeName", "Synthetic exchange", "exchange")
        + "</td></tr></table>"
    ).encode()


def test_explicit_security_row_can_bind_compatible_property_axes():
    assert (
        periodic_anchor(dimensional_cover(), metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)[
            "security_title"
        ]
        == "Class A Common Stock"
    )


@pytest.mark.parametrize(
    "body",
    [
        dimensional_cover(
            symbol_axis="us-gaap:StatementClassOfStockAxis", symbol_member="synthetic:ClassBMember"
        ),
        dimensional_cover(symbol_axis="us-gaap:DebtInstrumentAxis"),
        dimensional_cover(symbol_axis="synthetic:UnknownAxis"),
        dimensional_cover(exchange_day="2022-11-02"),
        dimensional_cover(exchange_day="2023-01-01"),
        dimensional_cover().replace(b"xbrldi:explicitMember", b"xbrldi:typedMember"),
    ],
)
def test_row_contexts_cannot_mix_classes_periods_or_unknown_axes(body):
    with pytest.raises(IdentityEvidenceError, match="class_context"):
        periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)


def test_colon_labelled_cover_binds_class_and_spaced_cusip():
    o = ownership_identity(
        colon_cover(cusip="123456 78 2"), metadata("SC 13G"), cik=CIK, cutoff=CUTOFF
    )
    assert o["cusip"] == CUSIP
    assert o["security_title"] == "Class A Common Stock"
    assert join_identity(anchors()[0], o)["security_id"] == "cusip:" + CUSIP


def class_qualified_cover(
    issuer="Synthetic Issuer Class B", title="Common Stock", subject_name="Synthetic Issuer"
):
    header = submission_header().replace(
        b"\t\tCENTRAL INDEX KEY:",
        f"\t\tCOMPANY CONFORMED NAME: {subject_name}\n\t\tCENTRAL INDEX KEY:".encode(),
        1,
    )
    cover = colon_cover(title=title).replace(b"Synthetic issuer", issuer.encode())
    return header + cover


def test_adjacent_issuer_class_suffix_is_bound_to_the_explicit_subject_name():
    body = class_qualified_cover()
    result = ownership_identity(body, metadata("SC 13G"), cik=CIK, cutoff=CUTOFF)
    assert result["security_title"] == "Class B Common Stock"
    binding = result["issuer_field_class_bindings"][0]
    assert binding["reported_security_title"] == "Common Stock"
    assert binding["reported_issuer_field"] == "Synthetic Issuer Class B"
    assert binding["class_qualifier"] == "B"
    assert (
        binding["SEC_header_sha256"]
        == ownership_subject(body, cik=CIK, accession=metadata()["accessionNumber"])[
            "SEC_header_sha256"
        ]
    )
    primary = periodic_anchor(
        periodic(title="Class B Common Stock"), metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF
    )
    assert join_identity(primary, result)["security_id"] == "cusip:" + CUSIP


@pytest.mark.parametrize(
    "issuer,title,subject_name",
    [
        ("Different Issuer Class B", "Common Stock", "Synthetic Issuer"),
        ("Synthetic Issuer Class B", "Class A Common Stock", "Synthetic Issuer"),
        ("Synthetic Issuer Class A and Class B", "Common Stock", "Synthetic Issuer"),
        ("Synthetic Issuer Class B (non-voting)", "Common Stock", "Synthetic Issuer"),
    ],
)
def test_issuer_suffix_cannot_override_subject_class_or_ambiguous_fields(
    issuer, title, subject_name
):
    with pytest.raises(IdentityEvidenceError):
        ownership_identity(
            class_qualified_cover(issuer, title, subject_name),
            metadata("SC 13G"),
            cik=CIK,
            cutoff=CUTOFF,
        )


def test_class_suffix_requires_subject_header_accession_and_legal_name():
    body = class_qualified_cover()
    for invalid in (
        body.replace(b"<SEC-HEADER>", b"<not-a-header>"),
        body.replace(
            b"COMPANY CONFORMED NAME: Synthetic Issuer", b"MISSING SUBJECT NAME: Synthetic Issuer"
        ),
        body.replace(
            b"ACCESSION NUMBER: 0000000001-22-000001", b"ACCESSION NUMBER: 0000000001-22-000002"
        ),
        body.replace(b"CENTRAL INDEX KEY: 0000000001", b"CENTRAL INDEX KEY: 0000000003"),
    ):
        with pytest.raises(IdentityEvidenceError):
            ownership_identity(invalid, metadata("SC 13G"), cik=CIK, cutoff=CUTOFF)


def test_a_class_word_in_the_actual_legal_name_does_not_supply_a_security_class():
    result = ownership_identity(
        class_qualified_cover(
            issuer="Synthetic Issuer Class B", subject_name="Synthetic Issuer Class B"
        ),
        metadata("SC 13G"),
        cik=CIK,
        cutoff=CUTOFF,
    )
    assert result["security_title"] == "Common Stock"
    assert "issuer_field_class_bindings" not in result


def test_a_later_cover_cannot_borrow_an_earlier_cover_issuer_field():
    body = (
        class_qualified_cover()
        + b"<p>Title of Class of Securities: Common Stock</p><p>CUSIP Number: 123456782</p>"
    )
    with pytest.raises(IdentityEvidenceError, match="unbound_ownership_cover_class"):
        ownership_identity(body, metadata("SC 13G"), cik=CIK, cutoff=CUTOFF)


@pytest.mark.parametrize(
    "title", ["Class B Non-voting Common Stock", "Class B Ordinary Shares", "Class A Common Stock"]
)
def test_named_class_evidence_preserves_voting_family_and_other_class_refusals(title):
    result = ownership_identity(class_qualified_cover(), metadata("SC 13G"), cik=CIK, cutoff=CUTOFF)
    primary = periodic_anchor(
        periodic(title=title), metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF
    )
    with pytest.raises(IdentityEvidenceError, match="share_class"):
        join_identity(primary, result)


def test_narrative_class_or_unbound_colon_fields_cannot_supply_identity():
    for body in (
        colon_cover().replace(b"Name of issuer:", b"Item 1(a) Name of issuer:"),
        colon_cover().replace(b"CUSIP Number:", b"Item 2(e) CUSIP Number:"),
    ):
        with pytest.raises(IdentityEvidenceError, match="unbound"):
            ownership_identity(body, metadata("SC 13G"), cik=CIK, cutoff=CUTOFF)
    with pytest.raises(IdentityEvidenceError, match="missing_ownership_class"):
        ownership_identity(
            colon_cover().replace(b"Title of Class", b"Item 2(d) Title of Class"),
            metadata("SC 13G"),
            cik=CIK,
            cutoff=CUTOFF,
        )


def test_conflicting_colon_covers_preserve_class_and_cusip_ambiguity():
    with pytest.raises(IdentityEvidenceError, match="ambiguous_ownership_class"):
        ownership_identity(
            colon_cover() + colon_cover(title="Class B Common Stock"),
            metadata("SC 13G"),
            cik=CIK,
            cutoff=CUTOFF,
        )
    with pytest.raises(IdentityEvidenceError, match="ambiguous_cusip"):
        ownership_identity(
            colon_cover() + colon_cover(cusip="234567899"),
            metadata("SC 13G"),
            cik=CIK,
            cutoff=CUTOFF,
        )


@pytest.mark.parametrize("title", ["Class C Capital Stock", "Registered Shares"])
def test_explicit_additional_equity_families_join_only_the_same_family(title):
    p = periodic_anchor(periodic(title=title), metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)
    o = ownership_identity(ownership(title=title), metadata("SC 13G"), cik=CIK, cutoff=CUTOFF)
    assert join_identity(p, o)["security_title"] == title
    with pytest.raises(IdentityEvidenceError, match="share_class"):
        join_identity(p, {**o, "security_title": "Class C Common Stock"})


def test_common_par_value_shorthand_is_not_a_general_common_word_alias():
    assert class_signature("Common, $1.00 Par Value") == class_signature("Common Stock")
    for title in ("Common Agreement", "Common Units", "Registered Shares and Ordinary Shares"):
        with pytest.raises(IdentityEvidenceError):
            class_signature(title)


@pytest.mark.parametrize("reported", ['"OLD"', "\u201cOLD\u201d", "OLD (NYSE)"])
def test_literal_cover_symbol_typography_retains_the_reported_spelling(reported):
    p = periodic_anchor(periodic(symbol=reported), metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)
    assert p["symbol_at_cutoff"] == "OLD"
    assert p["reported_symbols_at_filing"] == [reported]
    assert join_identity(p, anchors()[1])["reported_symbols_at_filing"] == [reported]


def test_dotted_share_class_spelling_requires_an_explicit_matching_class():
    with pytest.raises(IdentityEvidenceError, match="class_context"):
        periodic_anchor(periodic(symbol="OLDB"), metadata(), cik=CIK, symbol="OLD.B", cutoff=CUTOFF)
    p = periodic_anchor(
        periodic(symbol="OLDB", title="Class B Common Stock"),
        metadata(),
        cik=CIK,
        symbol="OLD.B",
        cutoff=CUTOFF,
    )
    assert p["symbol_at_cutoff"] == "OLD.B"
    assert p["reported_symbols_at_filing"] == ["OLDB"]
    with pytest.raises(IdentityEvidenceError, match="class_context"):
        periodic_anchor(
            periodic(symbol="NEWB", title="Class B Common Stock"),
            metadata(),
            cik=CIK,
            symbol="OLD.B",
            cutoff=CUTOFF,
        )


def multiple_listing_cover(second_title="Class A Common Stock", second_day="2022-11-01"):
    return (
        fact("EntityCentralIndexKey", CIK, "issuer")
        + dimension_context("one", "dei:EntityListingsExchangeAxis", "synthetic:VenueOne")
        + dimension_context(
            "two", "dei:EntityListingsExchangeAxis", "synthetic:VenueTwo", second_day
        )
        + "<table>"
        + cover_row(context="one").replace("Synthetic exchange", "Synthetic venue one")
        + cover_row(title=second_title, context="two").replace(
            "Synthetic exchange", "Synthetic venue two"
        )
        + "</table>"
    ).encode()


def test_same_security_multiple_explicit_venues_are_preserved_without_guessing_primary():
    p = periodic_anchor(multiple_listing_cover(), metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)
    assert p["exchange_at_filing"] is None
    assert p["exchanges_at_filing"] == ["Synthetic venue one", "Synthetic venue two"]
    joined = join_identity(p, anchors()[1])
    assert joined["exchange_at_filing"] is None
    assert joined["exchanges_at_filing"] == p["exchanges_at_filing"]
    assert joined["security_id"] == "cusip:" + CUSIP


@pytest.mark.parametrize(
    "body",
    [
        multiple_listing_cover(second_title="Class B Common Stock"),
        multiple_listing_cover(second_day="2022-11-02"),
        multiple_listing_cover(second_day="2023-01-01"),
        multiple_listing_cover().replace(
            b"dei:EntityListingsExchangeAxis", b"synthetic:UnknownAxis"
        ),
        multiple_listing_cover().replace(b"xbrldi:explicitMember", b"xbrldi:typedMember"),
        multiple_listing_cover().replace(b"synthetic:VenueTwo", b""),
    ],
)
def test_multiple_venues_cannot_hide_class_period_or_dimension_conflicts(body):
    with pytest.raises(IdentityEvidenceError, match="class_context"):
        periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)


def test_separate_explicit_debt_context_cannot_contaminate_equity_identity():
    body = (
        periodic(title="Class A Common Stock")
        + (
            dimension_context(
                "common", "us-gaap:StatementClassOfStockAxis", "synthetic:ClassAMember"
            )
            + dimension_context("debt", "us-gaap:StatementClassOfStockAxis", "synthetic:DebtMember")
            + "<table>"
            + cover_row(title="3.125% Notes due 2028", context="debt")
            + "</table>"
        ).encode()
    )
    p = periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)
    assert p["security_title"] == "Class A Common Stock"
    assert join_identity(p, anchors()[1])["security_id"] == "cusip:" + CUSIP


def test_only_debt_or_debt_blended_into_equity_context_is_still_refused():
    for body in (
        periodic(title="3.125% Notes due 2028"),
        periodic() + fact("Security12bTitle", "3.125% Notes due 2028").encode(),
    ):
        with pytest.raises(IdentityEvidenceError):
            periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)


def test_issuer_extension_ticker_axis_requires_declared_namespace_and_exact_member():
    body = dimensional_cover(
        symbol_axis="synthetic:TradingSymbolAxis", symbol_member="synthetic:OLDMember"
    )
    declared = b'<html xmlns:synthetic="https://issuer.invalid/2022">' + body + b"</html>"
    assert (
        periodic_anchor(declared, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)[
            "security_title"
        ]
        == "Class A Common Stock"
    )
    for refused in (
        body,
        declared.replace(b"synthetic:OLDMember", b"synthetic:OTHERMember"),
        declared.replace(b"synthetic:TradingSymbolAxis", b"synthetic:DebtInstrumentAxis"),
        declared.replace(b"https://issuer.invalid/2022", b"not-a-namespace-URI"),
    ):
        with pytest.raises(IdentityEvidenceError, match="class_context"):
            periodic_anchor(refused, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)


def test_larger_retained_document_is_fully_checked_instead_of_using_a_cover_prefix():
    body = periodic() + b" " * (16 * 1024 * 1024)
    with pytest.raises(IdentityEvidenceError, match="excessive_document"):
        periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)
    assert (
        periodic_anchor(
            body,
            metadata(),
            cik=CIK,
            symbol="OLD",
            cutoff=CUTOFF,
            maximum_document_bytes=MAX_RETAINED_DOCUMENT_BYTES,
        )["security_title"]
        == "Class A Common Stock, $0.01 par value"
    )
    contradictory = body + fact("Security12bTitle", "Class B Common Stock").encode()
    with pytest.raises(IdentityEvidenceError, match="class_context"):
        periodic_anchor(
            contradictory,
            metadata(),
            cik=CIK,
            symbol="OLD",
            cutoff=CUTOFF,
            maximum_document_bytes=MAX_RETAINED_DOCUMENT_BYTES,
        )
    with pytest.raises(IdentityEvidenceError, match="invalid_document_byte_bound"):
        periodic_anchor(
            periodic(),
            metadata(),
            cik=CIK,
            symbol="OLD",
            cutoff=CUTOFF,
            maximum_document_bytes=MAX_RETAINED_DOCUMENT_BYTES + 1,
        )


def exhibit_parent(href="cover.htm"):
    return (
        fact("EntityCentralIndexKey", CIK, "issuer")
        + "<p>Securities registered pursuant to Section 12(b) of the Act "
        + "formatted in Inline XBRL: See Exhibit 99.01</p>"
        + f'<a href="{href}">99.01+</a>'
    ).encode()


def test_explicit_separate_registration_cover_is_a_request_for_evidence_not_an_anchor():
    body = exhibit_parent()
    with pytest.raises(RegistrationCoverExhibit) as caught:
        periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)
    assert caught.value.relative_href == "cover.htm"
    assert caught.value.primary_sha256 == sha256(body).hexdigest()


@pytest.mark.parametrize(
    "href", ["../cover.htm", "https://elsewhere.invalid/cover.htm", "/cover.htm", "cover.htm?q=1"]
)
def test_registration_cover_reference_cannot_escape_the_same_filing_directory(href):
    with pytest.raises(IdentityEvidenceError, match="invalid_registration_cover_exhibit_link"):
        periodic_anchor(exhibit_parent(href), metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)


def test_registration_exhibit_cannot_override_existing_structured_class_conflicts():
    body = exhibit_parent() + fact("Security12bTitle", "Class B Common Stock").encode()
    with pytest.raises(IdentityEvidenceError, match="class_context"):
        periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)
    body = exhibit_parent() + b'<a href="other.htm">99.01</a>'
    with pytest.raises(IdentityEvidenceError, match="ambiguous_registration_cover_exhibit_link"):
        periodic_anchor(body, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)


def shared_cover_documents():
    context = (
        '<xbrli:context id="common"><xbrli:entity>'
        f'<xbrli:identifier scheme="http://www.sec.gov/CIK">{CIK}</xbrli:identifier>'
        '<xbrli:segment><xbrldi:explicitMember dimension="synthetic:TradingSymbolAxis">'
        "synthetic:OLDMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>"
        "<xbrli:period><xbrli:instant>2022-09-30</xbrli:instant></xbrli:period>"
        "</xbrli:context>"
    ).encode()
    parent = (
        b'<html xmlns:synthetic="https://issuer.invalid/2022">'
        + exhibit_parent()
        + context
        + b"</html>"
    )
    child = (
        fact("TradingSymbol", "OLD")
        + fact("Security12bTitle", "Class A Common Stock")
        + fact("SecurityExchangeName", "Synthetic exchange")
    ).encode()
    return parent, child, context


def test_explicit_inline_document_set_uses_only_exact_primary_context_references():
    parent, child, _ = shared_cover_documents()
    with pytest.raises(IdentityEvidenceError, match="document_issuer_mismatch"):
        periodic_anchor(child, metadata(), cik=CIK, symbol="OLD", cutoff=CUTOFF)
    result = periodic_anchor(
        child,
        metadata(),
        cik=CIK,
        symbol="OLD",
        cutoff=CUTOFF,
        shared_primary_document=parent,
        shared_cover_href="cover.htm",
    )
    assert result["document_sha256"] == sha256(child).hexdigest()
    binding = result["shared_registration_contexts"]
    assert binding["primary_document_sha256"] == sha256(parent).hexdigest()
    assert binding["relative_href"] == "cover.htm"
    assert binding["context_ids"] == ["common"]
    assert len(binding["context_resources_sha256"]) == 64
    # Input documents are never rewritten or presented as captured bytes.
    assert b"xbrli:context" not in child


@pytest.mark.parametrize(
    "failure",
    [
        "wrong_parent_issuer",
        "wrong_child_issuer",
        "wrong_href",
        "missing_context",
        "duplicate_parent_context",
        "duplicate_child_context",
        "future_context",
        "unknown_axis",
        "typed_axis",
        "undeclared_namespace",
        "conflicting_cover",
        "conflicting_valid_periods",
    ],
)
def test_shared_context_resolution_refuses_conflicting_missing_or_late_evidence(failure):
    parent, child, context = shared_cover_documents()
    href = "cover.htm"
    if failure == "wrong_parent_issuer":
        parent = parent.replace(CIK.encode(), b"0000000002")
    elif failure == "wrong_child_issuer":
        child += fact("EntityCentralIndexKey", "2").encode()
    elif failure == "wrong_href":
        href = "other.htm"
    elif failure == "missing_context":
        parent = parent.replace(context, b"")
    elif failure == "duplicate_parent_context":
        parent += context
    elif failure == "duplicate_child_context":
        child += context
    elif failure == "future_context":
        parent = parent.replace(b"2022-09-30", b"2023-09-30")
    elif failure == "unknown_axis":
        parent = parent.replace(b"synthetic:TradingSymbolAxis", b"synthetic:UnknownAxis")
    elif failure == "typed_axis":
        parent = parent.replace(b"xbrldi:explicitMember", b"xbrldi:typedMember")
    elif failure == "undeclared_namespace":
        parent = parent.replace(b' xmlns:synthetic="https://issuer.invalid/2022"', b"")
    elif failure == "conflicting_valid_periods":
        parent += context.replace(b'id="common"', b'id="other"').replace(
            b"2022-09-30", b"2022-10-31"
        )
        child += child.replace(b'contextRef="common"', b'contextRef="other"')
    else:
        parent += fact("Security12bTitle", "Class B Common Stock").encode()
    with pytest.raises(IdentityEvidenceError):
        periodic_anchor(
            child,
            metadata(),
            cik=CIK,
            symbol="OLD",
            cutoff=CUTOFF,
            shared_primary_document=parent,
            shared_cover_href=href,
        )
