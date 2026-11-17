from copy import deepcopy
from datetime import UTC, datetime

import pytest

from edgar_moe.data.historical_capital_definition import qualify_capital_definition
from edgar_moe.data.historical_identity import IdentityEvidenceError, join_identity

CIK = "0000000001"
OWN = "0000000002-22-000001"
PROXY = "0000000001-22-000002"
CUTOFF = datetime(2022, 12, 28, tzinfo=UTC)
DEFINITION = (
    "Which Example shares will be entitled to vote at the Annual Meeting? "
    "Example’s common stock ($0.20 par value capital stock) is the only class of security "
    "entitled to vote at the Annual Meeting. Each record owner and each stockholder who holds "
    "stock in street name at the close of business as of the record date is entitled to one vote "
    "for each share held at the meeting, or any adjournment or postponement."
)
HEADING = (
    "Security Ownership of Certain Beneficial Owners The following sets forth information as to "
    "any person known to Example to be the beneficial owner of more than five percent of "
    "Example’s common stock as of December 31, 2021."
)
TABLE = (
    "<table><tr><th>Name and address</th><th>Number of Shares Beneficially Owned</th>"
    "<th>Percent of Class</th></tr><tr><td>Example Manager Group (1) "
    "100 Example Boulevard Sample PA 12345</td><td>110</td><td>8.42%</td></tr></table>"
)
CITATION = (
    "(1) Based on the Schedule 13G filed with the Securities and Exchange Commission on "
    "February 9, 2022 by Example Manager Group and certain subsidiaries (Manager). Manager "
    "reported that it does not have sole voting power over any shares, and has shared voting "
    "power over 3 shares, sole dispositive power over 100 shares, and shared dispositive power "
    "over 10 shares."
)


def submission(accession, form, filename, day, role, name, cik, body, extra=""):
    return (
        f"<SEC-HEADER><ACCEPTANCE-DATETIME>{day}160000\n"
        f"ACCESSION NUMBER:\t{accession}\nCONFORMED SUBMISSION TYPE:\t{form}\n"
        f"FILED AS OF DATE:\t{day}\n{extra}"
        f"{role}:\n\tCOMPANY CONFORMED NAME:\t{name}\n\tCENTRAL INDEX KEY:\t{cik}\n"
        "\tIRS NUMBER:\t123456789\n</SEC-HEADER>"
        f"<DOCUMENT>\n<TYPE>{form}\n<FILENAME>{filename}\n<TEXT>{body}</TEXT>\n</DOCUMENT>"
    ).encode()


def sources():
    own_body = (
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
    own = submission(
        OWN,
        "SC 13G/A",
        "own.htm",
        "20220209",
        "FILED BY",
        "Example Manager Holdings Inc.",
        "0000000002",
        own_body,
        "SUBJECT COMPANY:\n\tCOMPANY CONFORMED NAME:\tExample Radio Corp\n"
        f"\tCENTRAL INDEX KEY:\t{CIK}\n",
    )
    proxy = submission(
        PROXY,
        "DEF 14A",
        "proxy.htm",
        "20220401",
        "FILER",
        "Example Radio Corporation",
        CIK,
        "Example Radio Corporation (Name of Registrant as Specified In Its Charter) "
        + DEFINITION
        + " "
        + HEADING
        + " "
        + TABLE
        + " "
        + CITATION,
    )
    row = {
        "cik": CIK,
        "form": "SC 13G/A",
        "accessionNumber": OWN,
        "primaryDocument": "own.htm",
        "acceptanceDateTime": "2022-02-09T21:00:00Z",
    }
    periodic = {
        "issuer_cik": CIK,
        "security_title": "Capital stock, par value $.20 per share",
        "symbol_at_cutoff": "SYN",
        "exchange_at_filing": "NYSE",
        "document_sha256": "a" * 64,
        "accession": "0000000001-22-000003",
        "available_by": "2022-11-08T00:00:00Z",
        "accepted_at": "2022-11-01T00:00:00Z",
        "form": "10-Q",
    }
    return periodic, own, row, proxy


def qualify(*args, cutoff=CUTOFF):
    return qualify_capital_definition(
        *args, proxy_accession=PROXY, proxy_filename="proxy.htm", cutoff=cutoff
    )


def test_explicit_definition_and_exact_citation_qualify_one_dated_title_correspondence():
    args = sources()
    original = deepcopy(args)
    own = qualify(*args)
    assert args == original
    assert own["security_title"] == args[0]["security_title"]
    assert own["available_by"] == "2022-04-15T00:00:00+00:00"
    proof = own["issuer_capital_definition_evidence"]
    assert proof["reported_ownership_title"] == "Common Stock"
    assert proof["ownership_event_date"] == "2021-12-31"
    assert proof["reporter_legal_name_sha256"] != proof["reporter_cover_name_sha256"]
    assert join_identity(args[0], own)["security_id"] == "cusip:123456782"


@pytest.mark.parametrize(
    "which,old,new",
    [
        (1, "Common Stock (Title", "Ordinary Shares (Title"),
        (1, "Common Stock (Title", "Class A Common Stock (Title"),
        (1, "Common Stock (Title", "Voting Common Stock (Title"),
        (1, "Common Stock (Title", "Common Stock, $0.01 par value (Title"),
        (1, "12-3456789", "12-3456780"),
        (1, "IRS NUMBER:\t123456789", "IRS NUMBER:\t000000000"),
        (1, "SOLE VOTING POWER 0", "SOLE VOTING POWER 1"),
        (1, "SHARED VOTING POWER 3", "SHARED VOTING POWER 4"),
        (1, "SOLE DISPOSITIVE POWER 100", "SOLE DISPOSITIVE POWER 101"),
        (1, "SHARED DISPOSITIVE POWER 10", "SHARED DISPOSITIVE POWER 11"),
        (1, "PERSON 110", "PERSON 111"),
        (1, "100 Example Blvd.", "101 Example Blvd."),
        (1, "PA 12345", "PA 12346"),
        (1, "December 31, 2021", "December 30, 2021"),
        (3, "$0.20 par value", "$0.25 par value"),
        (3, "only class of security", "one class of security"),
        (3, "one vote for each share", "two votes for each share"),
        (3, "Example Radio Corporation\n", "Another Radio Corporation\n"),
        (3, "Corporation (Name of Registrant", "Company (Name of Registrant"),
        (3, "CENTRAL INDEX KEY:\t0000000001", "CENTRAL INDEX KEY:\t0000000003"),
        (3, "common stock as of December 31, 2021", "common stock as of December 30, 2021"),
        (3, "Example’s common stock as of", "Other’s common stock as of"),
        (3, "(1) 100 Example Boulevard", "(2) 100 Example Boulevard"),
        (3, "<td>110</td>", "<td>111</td>"),
        (3, "<td>8.42%</td>", "<td>1000%</td>"),
        (3, "February 9, 2022", "February 8, 2022"),
        (3, "power over 3 shares", "power over 4 shares"),
        (3, "power over 100 shares", "power over 101 shares"),
        (3, "power over 10 shares", "power over 11 shares"),
        (3, "(Manager). Manager reported", "(Manager). Other reported"),
        (3, "100 Example Boulevard", "100 Example Avenue"),
        (3, "<table>", "A different security class. <table>"),
    ],
)
def test_conflicting_issuer_class_par_dates_identity_positions_and_table_binding_refuse(
    which, old, new
):
    args = list(sources())
    assert old.encode() in args[which]
    args[which] = args[which].replace(old.encode(), new.encode(), 1)
    with pytest.raises(IdentityEvidenceError):
        qualify(*args)


@pytest.mark.parametrize("extra", [DEFINITION, HEADING, TABLE, CITATION])
def test_duplicate_definition_heading_table_or_citation_is_not_resolved_by_first_match(extra):
    args = list(sources())
    args[3] = args[3].replace(b"</TEXT>", b" " + extra.encode() + b"</TEXT>")
    with pytest.raises(IdentityEvidenceError):
        qualify(*args)


@pytest.mark.parametrize(
    "title",
    [
        "Common Stock",
        "Ordinary Shares, $.20 par value",
        "Class A Capital Stock, $.20 par value",
        "Non-voting Capital Stock, $.20 par value",
        "Capital Stock",
    ],
)
def test_unrelated_periodic_classes_and_missing_par_cannot_use_the_definition(title):
    args = list(sources())
    args[0]["security_title"] = title
    with pytest.raises(IdentityEvidenceError):
        qualify(*args)


def test_definition_available_after_cutoff_cannot_retroactively_qualify_ownership():
    args = sources()
    with pytest.raises(IdentityEvidenceError):
        qualify(*args, cutoff=datetime(2022, 4, 10, tzinfo=UTC))


def test_plain_common_stock_mentions_are_not_an_explicit_equivalence():
    args = list(sources())
    args[3] = args[3].replace(
        DEFINITION.encode(), b"Common stock and capital stock are discussed separately."
    )
    with pytest.raises(IdentityEvidenceError, match="explicit_capital_definition"):
        qualify(*args)


def test_unicode_table_text_is_not_redecoded_with_a_guessed_single_byte_encoding():
    args = list(sources())
    args[3] = args[3].replace(b"Name and address", "\u200bName and address".encode())
    assert qualify(*args)["cusip"] == "123456782"
