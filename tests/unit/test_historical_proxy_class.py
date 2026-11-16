from copy import deepcopy
from datetime import UTC, datetime

import pytest

from edgar_moe.data.historical_identity import IdentityEvidenceError, class_signature, join_identity
from edgar_moe.data.historical_proxy_class import qualify_proxy_class

CUTOFF = datetime(2022, 12, 28, tzinfo=UTC)
ISSUER = "0000000001"
OWN_ACC = "0000000002-22-000001"
PROXY_ACC = "0000000001-22-000002"
CLAIM = (
    "According to the Schedule 13G (Amendment No. 6) filed on February 1, 2022, "
    "by Example Manager, Inc. (“Manager”), of the 1,100 shares of ERC’s Class A common stock "
    "beneficially owned, Manager has (a) sole voting power with respect to 700 shares, "
    "and (b) sole investment power with respect to all 1,100 shares."
)


def document(filename, form, text):
    return f"<DOCUMENT>\n<TYPE>{form}\n<FILENAME>{filename}\n<TEXT>{text}</TEXT>\n</DOCUMENT>"


def sources():
    metadata = {
        "cik": ISSUER,
        "form": "SC 13G/A",
        "accessionNumber": OWN_ACC,
        "acceptanceDateTime": "2022-02-01T22:00:00Z",
        "primaryDocument": "report.txt",
    }
    own_header = (
        "<ACCEPTANCE-DATETIME>20220201170000\n"
        f"ACCESSION NUMBER:\t{OWN_ACC}\nCONFORMED SUBMISSION TYPE:\tSC 13G/A\n"
        "FILED AS OF DATE:\t20220201\n"
        "SUBJECT COMPANY:\n\tCOMPANY CONFORMED NAME:\tExample Radio Corp\n"
        f"\tCENTRAL INDEX KEY:\t{ISSUER}\n"
        "FILED BY:\n\tCOMPANY CONFORMED NAME:\tExample Manager Inc.\n"
        "\tCENTRAL INDEX KEY:\t0000000002\n"
    )
    cover = (
        "(Amendment No: 6) Example Radio Corp (Name of Issuer) Common Stock "
        "(Title of Class of Securities) 123456782 (CUSIP Number) "
        "(5) Sole voting power 700 (6) Shared voting power 0 "
        "(7) Sole dispositive power 1100 (8) Shared dispositive power 0 "
        "(9) Aggregate amount beneficially owned by each reporting person 1100 (10)"
    )
    own = f"<SEC-HEADER>{own_header}</SEC-HEADER>" + document("report.txt", "SC 13G/A", cover)
    proxy_header = (
        "<ACCEPTANCE-DATETIME>20220330160000\n"
        f"ACCESSION NUMBER:\t{PROXY_ACC}\nCONFORMED SUBMISSION TYPE:\tDEF 14A\n"
        "FILED AS OF DATE:\t20220330\n"
        "FILER:\n\tCOMPANY CONFORMED NAME:\tExample Radio Corp\n"
        f"\tCENTRAL INDEX KEY:\t{ISSUER}\n"
    )
    proxy_body = (
        "<p>Vote the shares of Class A common stock of Example Radio Corporation (“ERC”).</p>"
        f"<p>{CLAIM}</p>"
    )
    proxy = f"<SEC-HEADER>{proxy_header}</SEC-HEADER>" + document(
        "proxy.htm", "DEF 14A", proxy_body
    )
    periodic = {
        "issuer_cik": ISSUER,
        "security_title": "Class A Common Stock, $0.01 par value",
        "symbol_at_cutoff": "SYN",
        "exchange_at_filing": "NYSE",
        "document_sha256": "a" * 64,
        "accession": "0000000001-22-000003",
        "available_by": "2022-11-08T00:00:00Z",
        "accepted_at": "2022-11-01T00:00:00Z",
        "form": "10-Q",
    }
    return periodic, own.encode(), metadata, proxy.encode()


def qualify(periodic, own, metadata, proxy, **kwargs):
    return qualify_proxy_class(
        periodic,
        own,
        metadata,
        proxy,
        proxy_accession=PROXY_ACC,
        proxy_filename="proxy.htm",
        cutoff=kwargs.get("cutoff", CUTOFF),
    )


def test_exact_issuer_citation_refines_only_missing_class_without_mutating_input():
    args = sources()
    before = deepcopy(args)
    result = qualify(*args)
    assert args == before
    assert class_signature(result["security_title"]) == ("common", "A", "")
    assert result["cusip"] == "123456782"
    proof = result["issuer_proxy_class_evidence"]
    assert proof["ownership_subject_evidence"]["subject_cik"] == ISSUER
    assert proof["reporter_cik"] == "0000000002"
    assert proof["proxy_reported_amendment"] == "6"
    assert proof["available_by"] == "2022-04-13T00:00:00+00:00"
    assert result["available_by"] == proof["available_by"]
    assert result["available_by"] > proof["reported_ownership_available_by"]
    assert join_identity(args[0], result)["security_id"] == "cusip:123456782"


@pytest.mark.parametrize(
    "which,old,new",
    [
        (1, b"Common Stock", b"Ordinary Shares"),
        (1, b"Common Stock", b"Class B Common Stock"),
        (1, b"Common Stock", b"Non-voting Common Stock"),
        (1, b"0000000001\nFILED BY", b"0000000003\nFILED BY"),
        (1, b"Example Manager Inc.", b"Another Manager Inc."),
        (1, b"FILED AS OF DATE:\t20220201", b"FILED AS OF DATE:\t20220202"),
        (1, b"(Amendment No: 6)", b"(Amendment No: 7)"),
        (1, b"Sole voting power 700", b"Sole voting power 701"),
        (1, b"Shared voting power 0", b"Shared voting power 1"),
        (1, b"Shared dispositive power 0", b"Shared dispositive power 1"),
        (1, b"reporting person 1100", b"reporting person 1101"),
        (3, b"CENTRAL INDEX KEY:\t0000000001", b"CENTRAL INDEX KEY:\t0000000003"),
        (3, b"Example Radio Corp\n", b"Different Radio Corp\n"),
        (3, b"Example Radio Corporation", b"Different Radio Corporation"),
        (3, b"ERC\xe2\x80\x99s Class A common", b"ERC\xe2\x80\x99s Class B common"),
        (3, b"February 1, 2022", b"February 2, 2022"),
        (3, b"of the 1,100 shares", b"of the 1,101 shares"),
        (3, b"respect to 700 shares", b"respect to 701 shares"),
        (3, b"respect to all 1,100 shares", b"respect to all 1,101 shares"),
        (3, b"DEF 14A", b"DEFA14A"),
        (3, b"proxy.htm", b"other.htm"),
        (3, b"<ACCEPTANCE-DATETIME>20220330160000", b"<ACCEPTANCE-DATETIME>20220331160000"),
        (3, b"<SEC-HEADER>", b"<SEC-HEADER><SEC-HEADER>"),
    ],
)
def test_conflicts_foreign_sources_missing_fields_and_changed_positions_refuse(which, old, new):
    args = list(sources())
    assert old in args[which]
    args[which] = args[which].replace(old, new)
    with pytest.raises(IdentityEvidenceError):
        qualify(*args)


@pytest.mark.parametrize(
    "title", ["Ordinary Shares", "Class B Common Stock", "Voting Class A Common Stock"]
)
def test_proxy_cannot_change_the_periodic_family_class_or_voting_right(title):
    args = list(sources())
    args[0] = args[0] | {"security_title": title}
    with pytest.raises(IdentityEvidenceError):
        qualify(*args)


def test_duplicate_issuer_citations_are_ambiguous_even_if_one_matches():
    args = list(sources())
    args[3] = args[3].replace(CLAIM.encode(), (CLAIM + " " + CLAIM).encode())
    with pytest.raises(IdentityEvidenceError, match="ambiguous_issuer_ownership"):
        qualify(*args)


def test_later_capture_or_filing_does_not_backdate_source_availability():
    with pytest.raises(IdentityEvidenceError, match="not_available_by_cutoff"):
        qualify(*sources(), cutoff=datetime(2022, 4, 1, tzinfo=UTC))
