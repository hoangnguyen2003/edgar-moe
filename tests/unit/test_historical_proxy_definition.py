from copy import deepcopy
from datetime import UTC, datetime

import pytest

from edgar_moe.data.historical_identity import IdentityEvidenceError, class_signature, join_identity
from edgar_moe.data.historical_proxy_definition import _proxy_text, qualify_proxy_definition

CIK = "0000000001"
OWN = "0000000002-22-000001"
PROXY = "0000000001-22-000002"
CUTOFF = datetime(2022, 12, 28, tzinfo=UTC)
DEFINITION = "We refer to our Class P common stock as our “common stock.”"
CITATION = (
    "Based on a Schedule 13G/A filed with the SEC by Example Manager Group, "
    "100 Example Blvd., Sample, PA 12345, on February 9, 2022, reflecting beneficial "
    "ownership as of December 31, 2021, Example Manager Group has shared voting power "
    "as to 3 shares of common stock, sole dispositive power as to 100 shares of common "
    "stock and shared dispositive power as to 10 shares of common stock."
)


def submission(accession, form, filename, date, role, name, cik, body, extra=""):
    return (
        f"<SEC-HEADER><ACCEPTANCE-DATETIME>{date}160000\n"
        f"ACCESSION NUMBER:\t{accession}\nCONFORMED SUBMISSION TYPE:\t{form}\n"
        f"FILED AS OF DATE:\t{date}\n{extra}"
        f"{role}:\n\tCOMPANY CONFORMED NAME:\t{name}\n\tCENTRAL INDEX KEY:\t{cik}\n"
        "\tIRS NUMBER:\t123456789\n</SEC-HEADER>"
        f"<DOCUMENT>\n<TYPE>{form}\n<FILENAME>{filename}\n<TEXT>{body}</TEXT>\n</DOCUMENT>"
    ).encode()


def sources():
    body = (
        "Example Radio Corp (Name of Issuer) Common Stock (Title of Class of Securities) "
        "123456782 (CUSIP Number) Date of Event Which Requires Filing of this Statement: "
        "December 31, 2021 "
        "1. NAME OF REPORTING PERSON I.R.S. IDENTIFICATION NO. OF ABOVE PERSON "
        "Example Manager Group - 12-3456789 2. CHECK BOX "
        "5. SOLE VOTING POWER 0 6. SHARED VOTING POWER 3 "
        "7. SOLE DISPOSITIVE POWER 100 8. SHARED DISPOSITIVE POWER 10 "
        "9. AGGREGATE AMOUNT BENEFICIALLY OWNED BY EACH REPORTING PERSON 110 10. CHECK BOX "
        "Item 2(b) – Address of Principal Business Office or, if none, residence: "
        "100 Example Blvd. Sample PA 12345 Item 2(c) – Citizenship: Pennsylvania"
    )
    ownership = submission(
        OWN,
        "SC 13G/A",
        "own.htm",
        "20220209",
        "FILED BY",
        "Example Manager Holdings Inc.",
        "0000000002",
        body,
        "SUBJECT COMPANY:\n\tCOMPANY CONFORMED NAME:\tExample Radio Corp\n"
        f"\tCENTRAL INDEX KEY:\t{CIK}\n",
    )
    proxy = submission(
        PROXY,
        "DEF 14A",
        "proxy.htm",
        "20220401",
        "FILER",
        "Example Radio Corp",
        CIK,
        DEFINITION + " " + CITATION,
    )
    metadata = {
        "cik": CIK,
        "form": "SC 13G/A",
        "accessionNumber": OWN,
        "primaryDocument": "own.htm",
        "acceptanceDateTime": "2022-02-09T21:00:00Z",
    }
    periodic = {
        "issuer_cik": CIK,
        "security_title": "Class P Common Stock",
        "symbol_at_cutoff": "SYN",
        "exchange_at_filing": "NYSE",
        "document_sha256": "a" * 64,
        "accession": "0000000001-22-000003",
        "available_by": "2022-11-08T00:00:00Z",
        "accepted_at": "2022-11-01T00:00:00Z",
        "form": "10-Q",
    }
    return periodic, ownership, metadata, proxy


def qualify(*args, cutoff=CUTOFF):
    return qualify_proxy_definition(
        *args, proxy_accession=PROXY, proxy_filename="proxy.htm", cutoff=cutoff
    )


def test_literal_definition_and_exact_reporter_positions_refine_only_the_missing_class():
    args = sources()
    before = deepcopy(args)
    own = qualify(*args)
    assert args == before
    assert class_signature(own["security_title"]) == ("common", "P", "")
    assert own["available_by"] == "2022-04-15T00:00:00+00:00"
    proof = own["issuer_proxy_definition_evidence"]
    assert own["available_by"] > proof["reported_ownership_available_by"]
    assert proof["reporter_cik"] == "0000000002"
    # A literal cover name is bound by the header's exact IRS identifier, never
    # by a global rule dropping corporate suffixes or changing legal names.
    assert proof["reporter_legal_name_sha256"] != proof["reporter_cover_name_sha256"]
    assert join_identity(args[0], own)["security_id"] == "cusip:123456782"


@pytest.mark.parametrize(
    "which,old,new",
    [
        (1, "Common Stock (Title", "Ordinary Shares (Title"),
        (1, "Common Stock (Title", "Class B Common Stock (Title"),
        (1, "Common Stock (Title", "Non-voting Common Stock (Title"),
        (1, "12-3456789", "12-3456780"),
        (1, "IRS NUMBER:\t123456789", "IRS NUMBER:\t123456780"),
        (1, "SOLE VOTING POWER 0", "SOLE VOTING POWER 1"),
        (1, "SHARED VOTING POWER 3", "SHARED VOTING POWER 4"),
        (1, "SOLE DISPOSITIVE POWER 100", "SOLE DISPOSITIVE POWER 101"),
        (1, "SHARED DISPOSITIVE POWER 10", "SHARED DISPOSITIVE POWER 11"),
        (1, "PERSON 110", "PERSON 111"),
        (1, "100 Example Blvd.", "101 Example Blvd."),
        (1, "December 31, 2021", "December 30, 2021"),
        (3, "Class P common stock", "Class Q common stock"),
        (3, "Example Radio Corp\n", "Another Radio Corp\n"),
        (3, "CENTRAL INDEX KEY:\t0000000001", "CENTRAL INDEX KEY:\t0000000003"),
        (3, "February 9, 2022", "February 8, 2022"),
        (3, "December 31, 2021", "December 30, 2021"),
        (3, "as to 3 shares", "as to 4 shares"),
        (3, "as to 100 shares", "as to 101 shares"),
        (3, "as to 10 shares", "as to 11 shares"),
        (3, "Example Manager Group", "Another Manager Group"),
        (3, "100 Example Blvd.", "101 Example Blvd."),
    ],
)
def test_identity_class_address_date_and_position_conflicts_fail_closed(which, old, new):
    args = list(sources())
    assert old.encode() in args[which]
    args[which] = args[which].replace(old.encode(), new.encode())
    with pytest.raises(IdentityEvidenceError):
        qualify(*args)


@pytest.mark.parametrize("text", [DEFINITION, CITATION])
def test_duplicate_definitions_or_citations_are_not_arbitrarily_selected(text):
    args = list(sources())
    args[3] = args[3].replace(text.encode(), (text + " " + text).encode())
    with pytest.raises(IdentityEvidenceError):
        qualify(*args)


def test_proxy_definition_does_not_backdate_late_evidence():
    with pytest.raises(IdentityEvidenceError, match="not_available_by_cutoff"):
        qualify(*sources(), cutoff=datetime(2022, 4, 14, tzinfo=UTC))


@pytest.mark.parametrize(
    "title", ["Ordinary Shares", "Class Q Common Stock", "Voting Class P Common Stock"]
)
def test_registered_family_class_and_voting_rights_cannot_be_overridden(title):
    args = list(sources())
    args[0] = args[0] | {"security_title": title}
    with pytest.raises(IdentityEvidenceError):
        qualify(*args)


@pytest.mark.parametrize(
    "title", ["Ordinary Shares", "Class P Common Stock", "Non-voting Common Stock"]
)
def test_explicit_ownership_classes_and_foreign_equity_families_cannot_use_the_definition(title):
    args = list(sources())
    args[1] = args[1].replace(
        b"Common Stock (Title of Class", (title + " (Title of Class").encode()
    )
    with pytest.raises(IdentityEvidenceError):
        qualify(*args)


def test_a_second_reporter_cover_cannot_be_ignored_even_if_the_first_matches():
    args = list(sources())
    marker = b"1. NAME OF REPORTING PERSON I.R.S. IDENTIFICATION NO. OF ABOVE PERSON Example Manager Group - 12-3456789 2."
    args[1] = args[1].replace(marker, marker + b" " + marker)
    with pytest.raises(IdentityEvidenceError, match="cover_header_identity_mismatch"):
        qualify(*args)


def test_a_zero_placeholder_cannot_link_a_cover_name_to_a_different_legal_filer_name():
    args = list(sources())
    args[1] = args[1].replace(b"123456789", b"000000000").replace(b"12-3456789", b"00-0000000")
    with pytest.raises(IdentityEvidenceError, match="cover_header_identity_mismatch"):
        qualify(*args)


def page(content, number):
    return f'<div style="page-break-after:always;"><div><a href="#TOC">TABLE OF CONTENTS</a></div><div>{content}</div><div><div style="text-align:center;">{number}</div></div></div>'


def test_structural_page_furniture_can_interrupt_a_reporter_name_without_changing_facts():
    args = list(sources())
    first, second = CITATION.split("Group has")
    pages = page(DEFINITION + " " + first, 1) + page("Group has" + second, 2)
    args[3] = args[3].replace((DEFINITION + " " + CITATION).encode(), pages.encode())
    result = qualify(*args)
    assert result["issuer_proxy_definition_evidence"]["numbered_pages"] == 2


def test_nonconsecutive_footers_and_arbitrary_header_text_are_not_silently_removed():
    with pytest.raises(IdentityEvidenceError, match="nonconsecutive"):
        _proxy_text((page("first", 1) + page("second", 3)).encode())
    text, proof = _proxy_text(
        page("Business count 100", 1).replace("TABLE OF CONTENTS", "Class B common stock").encode()
    )
    assert "Class B common stock" in text and "Business count 100" in text
    assert len(proof["removed_page_furniture_sha256"]) == 1
