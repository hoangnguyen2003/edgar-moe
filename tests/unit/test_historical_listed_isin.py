from argparse import Namespace

import pytest

from edgar_moe.data.historical_identity import IdentityEvidenceError, instant
from edgar_moe.data.historical_listed_isin import listing, nport_identity
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import audit_historical_checkpoint as audit
from scripts import enrich_historical_listed_isin as stage

CUTOFF = instant("2022-12-28T23:23:50Z")
PERIODIC = {
    "issuer_cik": "0000000001",
    "symbol_at_cutoff": "SYN",
    "security_title": "Ordinary shares (€0.001 nominal value per share)",
    "exchange_at_filing": "New York Stock Exchange",
    "available_by": "2022-11-01T00:00:00Z",
}
ISSUER = {"accession": "0000000001-18-000001", "primary": "main.htm", "exhibit": "exhibit.htm"}
FUND = {"accession": "0000000002-22-000001", "filer_cik": "0000000002"}
ISIN = "US1234567824"


def filings():
    path = "/Archives/edgar/data/1/000000000118000001/"
    index = f"""<html><p>Form 8-K - Current report</p><p>SEC Accession No. {ISSUER["accession"]}</p>
    <div class="companyInfo">Synthetic PLC (Filer) CIK: 0000000001 (see all company filings)</div>
    <div><div class="infoHead">Accepted</div><div>2018-10-31 10:10:18</div>
      <div class="infoHead">Filing Date</div><div>2018-10-31</div></div>
    <table>
      <tr><td>1</td><td>8-K</td><td><a href="{path}main.htm">main.htm</a></td><td>8-K</td><td>100</td></tr>
      <tr><td>2</td><td>99.1</td><td><a href="{path}exhibit.htm">exhibit.htm</a></td><td>EX-99.1</td><td>300</td></tr>
    </table></html>"""
    current = """<html>
    <p>One ordinary share, nominal value €0.001 per share, of Synthetic PLC (the “Synthetic PLC Shares”).</p>
    <p>The Synthetic PLC Shares will trade on the New York Stock Exchange (“NYSE”) and
      the Frankfurt Stock Exchange (“FSE”) under the ticker symbol “SYN”.</p>
    <table><tr><td>99.1</td><td><a href="exhibit.htm">European Listing Prospectus, published in October.</a></td></tr></table>
    </html>"""
    exhibit = f"""<html><table><tr><td>
    <p>When admitted to trading, the Synthetic PLC Shares will be registered with:</p>
    <p>International Securities Identification Number (ISIN): {ISIN};</p>
    <p>German Securities Identification Number (<i>Wertpapierkennnummer,</i> WKN): ABC DEF;</p>
    <p>Ticker Symbol: SYN.</p>
    </td></tr></table></html>"""
    return tuple(s.encode() for s in (index, current, exhibit))


def issuer_listing():
    return listing(*filings(), ISSUER, PERIODIC, issuer_name="Synthetic PLC", cutoff=CUTOFF)


def fund():
    return b"""<SEC-HEADER>
<ACCEPTANCE-DATETIME>20221028134059
ACCESSION NUMBER: 0000000002-22-000001
CONFORMED SUBMISSION TYPE: NPORT-P
PUBLIC DOCUMENT COUNT: 1
CONFORMED PERIOD OF REPORT: 20220831
FILED AS OF DATE: 20221028
FILER:
    CENTRAL INDEX KEY: 0000000002
</SEC-HEADER>
<DOCUMENT>
<TYPE>NPORT-P
<FILENAME>primary_doc.xml
<TEXT><XML><edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
<headerData><submissionType>NPORT-P</submissionType><isConfidential>false</isConfidential></headerData>
<formData>
 <genInfo><regCik>0000000002</regCik><repPdDate>2022-08-31</repPdDate><repPdEnd>2022-08-31</repPdEnd></genInfo>
 <invstOrSecs><invstOrSec><name>Synthetic PLC</name><title>ORDINARY SHARES</title><cusip>123456782</cusip>
  <identifiers><isin value="US1234567824"/></identifiers><units>NS</units><assetCat>EC</assetCat>
  <payoffProfile>Long</payoffProfile><isRestrictedSec>N</isRestrictedSec>
 </invstOrSec></invstOrSecs>
</formData></edgarSubmission></XML></TEXT>
</DOCUMENT>"""


def test_issuer_and_direct_fund_holding_share_the_same_isin():
    listed = issuer_listing()
    proof = nport_identity(fund(), FUND, listed, cutoff=CUTOFF)
    assert listed["isin"] == proof["isin"] == ISIN
    assert proof["cusip"] == "123456782"
    assert proof["available_by"] == "2022-11-04T17:40:59+00:00"
    assert proof["document_sha256"] == stage.digest(fund())


@pytest.mark.parametrize(
    ("which", "old", "new"),
    [
        (0, b"0000000001 (see", b"0000000002 (see"),
        (0, b">EX-99.1<", b">EX-99.2<"),
        (0, b"2018-10-31 10:10:18", b"2022-12-27 10:10:18"),
        (1, b"exhibit.htm", b"other.htm"),
        (1, "€0.001".encode(), "€0.002".encode()),
        (1, b"Frankfurt Stock Exchange", b"London Stock Exchange"),
        (2, b"US1234567824", b"US1234567828"),
        (2, b"Ticker Symbol: SYN.", b"Ticker Symbol: OLD."),
        (2, b"Number (ISIN):", b"Number (SEDOL):"),
    ],
)
def test_issuer_proof_refuses_wrong_listing_class_or_accession(which, old, new):
    raw = list(filings())
    assert old in raw[which]
    raw[which] = raw[which].replace(old, new)
    with pytest.raises(IdentityEvidenceError):
        listing(*raw, ISSUER, PERIODIC, issuer_name="Synthetic PLC", cutoff=CUTOFF)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b"<TYPE>NPORT-P", b"<TYPE>NPORT-P/A"),
        (b"20221028134059", b"20221227134059"),
        (b"<regCik>0000000002", b"<regCik>0000000003"),
        (b"<isConfidential>false", b"<isConfidential>true"),
        (b"2022-08-31</repPdEnd>", b"2022-08-30</repPdEnd>"),
        (b"<name>Synthetic PLC", b"<name>Other PLC"),
        (b"ORDINARY SHARES", b"CLASS A ORDINARY SHARES"),
        (b"US1234567824", b"US1234567828"),
        (b"<cusip>123456782", b"<cusip>123456781"),
        (b"<assetCat>EC", b"<assetCat>DE"),
        (b"<units>NS", b"<units>NC"),
        (b"<payoffProfile>Long", b"<payoffProfile>Short"),
        (b"</invstOrSec>", b"<derivativeInfo/></invstOrSec>"),
        (b"<XML>", b"<XML><!DOCTYPE edgarSubmission>"),
    ],
)
def test_fund_proof_refuses_late_wrong_or_derived_positions(old, new):
    assert old in fund()
    with pytest.raises(IdentityEvidenceError):
        nport_identity(fund().replace(old, new), FUND, issuer_listing(), cutoff=CUTOFF)


def test_registration_name_verifies_its_cik_context():
    data = b"""<xbrli:context id="x">
    <xbrli:identifier scheme="https://www.sec.gov/CIK">0000000001</xbrli:identifier>
    </xbrli:context>
    <ix:nonnumeric name="dei:EntityRegistrantName" contextref="x">Synthetic PLC</ix:nonnumeric>"""
    assert stage.registration_name(data, "0000000001") == "Synthetic PLC"
    with pytest.raises(IdentityEvidenceError, match="registrant_name_issuer"):
        stage.registration_name(data, "0000000002")


def test_checkpoint_only_replays_with_required_corroboration(tmp_path, monkeypatch):
    directory = tmp_path / "identities"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(audit, "ARTIFACT_ROOT", tmp_path)
    stored = {
        "kind": stage.KIND,
        "algorithms": stage.algorithms(),
        "baseline_filename": "baseline.json",
        "reference_plan_filename": "plan.json",
    }
    raw = canonical_bytes(stored)
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
        return stored

    monkeypatch.setattr(stage, "recompute", replay)
    assert audit.replay_identity(inputs)[0] == stored
    assert calls == [
        (directory, "baseline.json", "plan.json", *(tmp_path / key for key in stage.SOURCE_ARGS))
    ]
    monkeypatch.setattr(stage, "recompute", lambda *args: stored | {"tampered": True})
    with pytest.raises(ValueError, match="identity_replay_mismatch"):
        audit.replay_identity(inputs)
    inputs.listed_isin_sources = None
    with pytest.raises(ValueError, match="missing_listed_isin_sources"):
        audit.replay_identity(inputs)
