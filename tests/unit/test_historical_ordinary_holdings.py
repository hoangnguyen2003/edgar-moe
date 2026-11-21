from copy import deepcopy
from datetime import UTC, datetime

import pytest
from test_historical_holdings_class import CUSIP, ISSUER, MANAGER, row, source

from edgar_moe.data.historical_identity import IdentityEvidenceError
from edgar_moe.data.historical_ordinary_holdings import filed_ordinary_holding, registrant_identity
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import enrich_historical_ordinary_holdings as supplement

CUTOFF = datetime(2022, 12, 28, tzinfo=UTC)
NAME = "Example & Co Inc"
COUNTRY = "Ireland"
TITLE = "Class A Ordinary Shares, $0.001 par value"
DISPLAY = "Example &amp; Co Inc Ireland SHS Class A"


def filing(rows=None):
    if rows is None:
        rows = [row("ORD USD0.001", name=DISPLAY), row("COM", option="<putCall>Put</putCall>")]
    payload, ref = source(rows=rows)
    for date in (b"2021", b"-21-"):
        payload = payload.replace(date, b"2022" if date == b"2021" else b"-22-")
    accession = ref["metadata"]["accessionNumber"].replace("-21-", "-22-")
    return payload, {"manager_cik": MANAGER, "accession": accession}


def extract(rows=None, *, cutoff=CUTOFF):
    payload, ref = filing(rows)
    return filed_ordinary_holding(
        payload,
        ref,
        issuer_name=NAME,
        issuer_country=COUNTRY.upper(),
        registered_title=TITLE,
        cusip=CUSIP,
        cutoff=cutoff,
    )


def test_matches_issuer_class_and_nominal_value_in_original_holdings():
    proof = extract()
    assert proof["available_by"] == "2022-11-15T00:00:00+00:00"
    assert proof["report_period"] == "2022-09-30"
    assert proof["rows"] == [
        {
            "reported_issuer_name": "Example & Co Inc Ireland SHS Class A",
            "reported_title": "ORD USD0.001",
        }
    ]


@pytest.mark.parametrize(
    "record",
    [
        row("ORD USD0.01", name=DISPLAY),
        row("ORD", name=DISPLAY),
        row("Ordinary Shares", name=DISPLAY.replace("Class A", "Class B")),
        row("Ordinary Shares", name=DISPLAY.replace("Ireland", "France")),
        row("Ordinary Shares", name=DISPLAY.replace(" Class A", "")),
        row("Common Stock", name=DISPLAY),
        row("Ordinary Shares", name=DISPLAY).replace("<sshPrnamt>20", "<sshPrnamt>0"),
    ],
)
def test_refuses_conflicting_class_even_when_an_equity_row_agrees(record):
    with pytest.raises(IdentityEvidenceError, match="issuer_or_class_conflict"):
        extract([row("Ordinary Shares", name=DISPLAY), record])


def test_refuses_derivative_only_late_amended_or_incomplete_filing():
    with pytest.raises(IdentityEvidenceError, match="missing_equity"):
        extract([row("Ordinary Shares", name=DISPLAY, option="<putCall>Call</putCall>")])
    with pytest.raises(IdentityEvidenceError, match="availability"):
        extract(cutoff=datetime(2022, 11, 14, tzinfo=UTC))
    payload, ref = filing()
    for before, after, error in [
        (b"<isAmendment>false", b"<isAmendment>true", "amendment"),
        (b"<isConfidentialOmitted>false", b"<isConfidentialOmitted>true", "cover_identity"),
        (b"<tableEntryTotal>2", b"<tableEntryTotal>1", "table_count"),
    ]:
        assert before in payload
        with pytest.raises(IdentityEvidenceError, match=error):
            filed_ordinary_holding(
                payload.replace(before, after),
                ref,
                issuer_name=NAME,
                registered_title=TITLE,
                cusip=CUSIP,
                cutoff=CUTOFF,
            )


def test_registrant_facts_are_scoped_to_the_requested_CIK():
    combined = f"""
    <xbrli:context id="parent"><xbrli:identifier scheme="https://www.sec.gov/CIK">{ISSUER}</xbrli:identifier></xbrli:context>
    <xbrli:context id="other"><xbrli:identifier scheme="https://www.sec.gov/CIK">{MANAGER}</xbrli:identifier></xbrli:context>
    <ix:nonnumeric contextRef="parent" name="dei:EntityRegistrantName">Example &amp; Co Inc</ix:nonnumeric>
    <ix:nonnumeric contextRef="parent" name="dei:EntityIncorporationStateCountryCode">Ireland</ix:nonnumeric>
    <ix:nonnumeric contextRef="other" name="dei:EntityRegistrantName">Other filer</ix:nonnumeric>
    """.encode()
    assert registrant_identity(combined, ISSUER) == (NAME.upper(), COUNTRY.upper())
    conflicting = (
        combined
        + b'<ix:nonnumeric contextRef="parent" name="dei:EntityRegistrantName">Other</ix:nonnumeric>'
    )
    with pytest.raises(IdentityEvidenceError, match="registrant_identity"):
        registrant_identity(conflicting, ISSUER)


def test_reference_plan_has_two_original_accessions_per_eligible_identity():
    _, first = filing()
    second = first | {"accession": "0000000002-22-000004"}
    plan = {
        "schema_version": 1,
        "scope": supplement.PLAN_SCOPE,
        "cases": [{"snapshot_member_key": "new", "reports": [first, second]}],
    }
    assert supplement.references(canonical_bytes(plan), {"new"}) == plan["cases"]
    duplicate = deepcopy(plan)
    duplicate["cases"][0]["reports"][1] = first
    for candidate, eligible in ((duplicate, {"new"}), (plan, {"different"})):
        with pytest.raises(IdentityEvidenceError):
            supplement.references(canonical_bytes(candidate), eligible)
