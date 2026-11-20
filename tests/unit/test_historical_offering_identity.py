from argparse import Namespace

import pytest

from edgar_moe.data.historical_identity import IdentityEvidenceError, instant
from edgar_moe.data.historical_offering_identity import ordinary_share_offering
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import audit_historical_checkpoint as audit
from scripts import enrich_historical_offering_identity as stage

CUTOFF = instant("2022-12-28T23:23:50Z")
REFERENCE = {"accession": "0000000001-20-000001", "filer_cik": "0000000001"}
PERIODIC = {
    "issuer_cik": "0000000001",
    "security_title": "Ordinary Shares, $0.01 par value per share",
    "symbol_at_cutoff": "SYN",
    "exchange_at_filing": "New York Stock Exchange",
    "available_by": "2022-11-15T23:23:50Z",
}


def offering():
    return """<SEC-HEADER>
<ACCEPTANCE-DATETIME>20200610063155
ACCESSION NUMBER: 0000000001-20-000001
CONFORMED SUBMISSION TYPE: FWP
PUBLIC DOCUMENT COUNT: 1
FILED AS OF DATE: 20200610
SUBJECT COMPANY:
    CENTRAL INDEX KEY: 0000000001
FILED BY:
    CENTRAL INDEX KEY: 0000000001
</SEC-HEADER>
<DOCUMENT>
<TYPE>FWP
<FILENAME>offering.htm
<TEXT><html><meta charset="UTF-8">
<p>10,000 Ordinary Shares, par value $0.01 per Share (the “Ordinary Shares”).</p>
<table>
    <tr><td>Issuer:</td><td>Synthetic PLC</td></tr>
    <tr><td>Ticker / Exchange for the Ordinary Shares:</td>
        <td>SYN / The New York Stock Exchange (“ NYSE ”).</td></tr>
</table>
<table>
    <tr><td>Terms Applicable to the Ordinary Shares Offering</td></tr>
    <tr><td>CUSIP / ISIN for the Ordinary Shares:</td><td> </td>
        <td>123456 782 / US1234567824</td></tr>
</table>
<table>
    <tr><td>Terms Applicable to the Preferred Shares Offering</td></tr>
    <tr><td>CUSIP / ISIN for the Preferred Shares:</td><td>000000 000 / US0000000002</td></tr>
</table></html></TEXT></DOCUMENT>""".encode()


def prove(raw):
    return ordinary_share_offering(
        raw, REFERENCE, PERIODIC, issuer_name="Synthetic PLC", cutoff=CUTOFF
    )


def test_current_registration_binds_the_exact_ordinary_share_offering():
    evidence = prove(offering())
    assert evidence["cusip"] == "123456782"
    assert evidence["isin"] == "US1234567824"
    assert evidence["available_by"] == "2020-06-17T10:31:55+00:00"
    assert evidence["document_sha256"] == stage.digest(offering())


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b"0000000001\n</SEC-HEADER>", b"0000000002\n</SEC-HEADER>"),
        (b"SUBJECT COMPANY:", b"ANOTHER COMPANY:"),
        (b"20200610063155", b"20221227063155"),
        (b"TYPE>FWP", b"TYPE>424B2"),
        (b"par value $0.01", b"par value $0.02"),
        (b"Ordinary Shares, par", b"Preferred Shares, par"),
        (b"SYN / The New York", b"NEW / The New York"),
        (b"Synthetic PLC", b"Other Company PLC"),
        (b"Terms Applicable to the Ordinary Shares Offering", b"Terms Applicable to All Offerings"),
        (b"CUSIP / ISIN for the Ordinary Shares:", b"CUSIP / ISIN for the Preferred Shares:"),
        (b"123456 782", b"123456 781"),
        (b"US1234567824", b"US1234567825"),
    ],
)
def test_wrong_issuer_date_listing_or_security_is_refused(old, new):
    assert old in offering()
    with pytest.raises(IdentityEvidenceError):
        prove(offering().replace(old, new))


def test_checkpoint_dispatch_requires_offering_and_rejects_tampering(tmp_path, monkeypatch):
    directory = tmp_path / "identities"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(audit, "ARTIFACT_ROOT", tmp_path)
    exact = {
        "kind": stage.KIND,
        "algorithms": stage.algorithms(),
        "baseline_filename": "baseline.json",
        "reference_plan_filename": "plan.json",
    }
    raw = canonical_bytes(exact)
    name = stage.PREFIX + "-" + audit.digest(raw) + ".json"
    path = directory / name
    path.write_bytes(raw)
    path.chmod(0o600)
    inputs = Namespace(
        directory=directory,
        identity_result=name,
        **{key.replace("-", "_"): tmp_path / key for key in stage.SOURCE_ARGS},
    )
    calls = []

    def replay(*args):
        calls.append(args)
        return exact

    monkeypatch.setattr(stage, "recompute", replay)
    assert audit.replay_identity(inputs)[0] == exact
    assert calls == [
        (directory, "baseline.json", "plan.json", *(tmp_path / key for key in stage.SOURCE_ARGS))
    ]
    monkeypatch.setattr(stage, "recompute", lambda *args: exact | {"substituted": True})
    with pytest.raises(ValueError, match="identity_replay_mismatch"):
        audit.replay_identity(inputs)
    inputs.offering_identity_sources = None
    with pytest.raises(ValueError, match="missing_offering_identity_sources"):
        audit.replay_identity(inputs)
