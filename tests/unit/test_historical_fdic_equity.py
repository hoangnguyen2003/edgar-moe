from argparse import Namespace

import pytest

from edgar_moe.data.historical_fdic_equity import filed_equity_reference
from edgar_moe.data.historical_identity import IdentityEvidenceError, instant
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import audit_historical_checkpoint as audit
from scripts import enrich_historical_fdic_equity as stage

CUTOFF = instant("2022-12-28T23:23:50Z")
OFFER = {
    "accession": "0000000003-22-000001",
    "filer_cik": "0000000003",
    "filename": "offering.htm",
}
PERIODIC = {
    "accession": "FDIC:12345:7:1",
    "issuer_cik": "0000000001",
    "available_by": "2022-11-15T00:00:00+00:00",
    "certificate": 12345,
    "reported_issuer_name": "Synthetic State Bank",
    "security_title": "Common Stock, $0.01 par value",
    "symbol_at_cutoff": "SYN",
    "exchange_at_filing": "NASDAQ Global Select Market",
}


def filing():
    return b"""<SEC-HEADER>
<ACCEPTANCE-DATETIME>20220210122632
ACCESSION NUMBER: 0000000003-22-000001
CONFORMED SUBMISSION TYPE: 424B2
FILED AS OF DATE: 20220210
FILER:
    CENTRAL INDEX KEY: 0000000003
FILER:
    CENTRAL INDEX KEY: 0000000004
</SEC-HEADER>
<DOCUMENT>
<TYPE>424B2
<FILENAME>offering.htm
<TEXT><html><body>
  <div><p>Other Retained Offering Overview</p>
    <table><tr><td>Note (CUSIP 123456790)</td></tr></table></div>
  <div><p>Synthetic State Bank Overview</p>
    <p>Information filed with the FDIC by Synthetic State Bank pursuant to the
       Exchange Act can be located by reference to the FDIC certificate number 12345.</p>
    <p>Information as of market close on February 8, 2022:</p>
    <table><tr><td>Bloomberg Ticker Symbol:</td><td>SYN</td></tr>
      <tr><td>Exchange:</td><td>Nasdaq</td></tr></table>
    <table><tr><td> </td></tr><tr>
      <td>Common Stock of Synthetic State Bank (CUSIP 123456782)</td>
      <td>High ($)</td><td>Low ($)</td><td>Dividends ($)</td>
    </tr></table>
  </div>
</body></html></TEXT>
</DOCUMENT>"""


def test_equity_reference_matches_fdic_certificate_ticker_exchange_and_class():
    proof = filed_equity_reference(filing(), OFFER, PERIODIC, cutoff=CUTOFF)
    assert proof["cusip"] == "123456782"
    assert proof["document_sha256"] == stage.digest(filing())
    assert proof["available_by"] == "2022-02-17T17:26:32+00:00"
    assert proof["source_observation_date"] == "2022-02-08"


@pytest.mark.parametrize(
    ("old", "replacement"),
    [
        (b"20220210\nFILER", b"20220211\nFILER"),
        (b"20220210122632", b"20221227122632"),
        (b"0000000004\n</SEC-HEADER>", b"0000000001\n</SEC-HEADER>"),
        (b"CONFORMED SUBMISSION TYPE: 424B2", b"CONFORMED SUBMISSION TYPE: FWP"),
        (b"<FILENAME>offering.htm", b"<FILENAME>fee.htm"),
        (b"certificate number 12345", b"certificate number 12346"),
        (b"by Synthetic State Bank pursuant", b"by Other Bank pursuant"),
        (b"<td>SYN</td>", b"<td>SYN.A</td>"),
        (b"<td>Nasdaq</td>", b"<td>NYSE</td>"),
        (b"February 8, 2022:", b"February 11, 2022:"),
        (b"Common Stock of", b"Preferred Stock of"),
        (b"123456782", b"123456781"),
        (b"<td> </td>", b"<td>Note (CUSIP 123456790)</td>"),
        (
            b"<div><p>Synthetic State Bank Overview",
            b"<div><p>Unrelated</p><p>Synthetic State Bank Overview",
        ),
    ],
)
def test_wrong_prospectus_or_equity_evidence_is_refused(old, replacement):
    assert old in filing()
    with pytest.raises(IdentityEvidenceError):
        filed_equity_reference(filing().replace(old, replacement), OFFER, PERIODIC, cutoff=CUTOFF)


def test_late_prospectus_and_wrong_periodic_class_are_refused():
    with pytest.raises(IdentityEvidenceError, match="unavailable_at_cutoff"):
        filed_equity_reference(
            filing(),
            OFFER,
            PERIODIC | {"available_by": "2022-02-15T00:00:00Z"},
            cutoff=instant("2022-02-16T00:00:00Z"),
        )
    with pytest.raises(IdentityEvidenceError, match="periodic_anchor"):
        filed_equity_reference(
            filing(), OFFER, PERIODIC | {"security_title": "Class A Common Stock"}, cutoff=CUTOFF
        )


def test_plan_can_target_only_an_unresolved_periodic_member():
    plan = {
        "schema_version": 1,
        "scope": stage.PLAN_SCOPE,
        "snapshot_member_key": "a" * 64,
        "certificate": 12345,
        "prospectus": OFFER,
    }
    raw = canonical_bytes(plan)
    assert stage.reference(raw, {"a" * 64}) == plan
    with pytest.raises(IdentityEvidenceError, match="plan"):
        stage.reference(raw, {"b" * 64})
    with pytest.raises(IdentityEvidenceError, match="reference"):
        stage.reference(
            canonical_bytes(plan | {"prospectus": OFFER | {"filename": "../offering.htm"}}),
            {"a" * 64},
        )


def test_checkpoint_route_replays_only_matching_prospectus_stage(tmp_path, monkeypatch):
    directory = tmp_path / "identities"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(audit, "ARTIFACT_ROOT", tmp_path)
    inputs = Namespace(
        directory=directory,
        **{name.replace("-", "_"): tmp_path / name for name in stage.SOURCE_ARGS},
    )
    stored = {
        "kind": stage.KIND,
        "algorithms": stage.algorithms(),
        "baseline_filename": "baseline.json",
        "reference_plan_filename": "plan.json",
    }
    raw = canonical_bytes(stored)
    inputs.identity_result = stage.PREFIX + "-" + audit.digest(raw) + ".json"
    path = directory / inputs.identity_result
    path.write_bytes(raw)
    path.chmod(0o600)
    calls = []

    def replay(*args):
        calls.append(args)
        return stored

    monkeypatch.setattr(stage, "recompute", replay)
    assert audit.replay_identity(inputs)[0] == stored
    assert calls == [
        (directory, "baseline.json", "plan.json", *(tmp_path / name for name in stage.SOURCE_ARGS))
    ]
    monkeypatch.setattr(stage, "recompute", lambda *args: stored | {"tampered": True})
    with pytest.raises(ValueError, match="identity_replay_mismatch"):
        audit.replay_identity(inputs)
    inputs.listed_equity_sources = None
    with pytest.raises(ValueError, match="missing_listed_equity_sources"):
        audit.replay_identity(inputs)
