from datetime import datetime

import pytest

from edgar_moe.data.historical_capital_stock_tags import qualify_tagged_capital_stock
from edgar_moe.data.historical_identity import IdentityEvidenceError, periodic_anchor
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import enrich_historical_capital_stock_tags as stage

CUTOFF = datetime.fromisoformat("2022-12-28T23:23:50+00:00")
METADATA = {
    "cik": "0000000001",
    "form": "10-Q",
    "accessionNumber": "0000000001-22-000001",
    "acceptanceDateTime": "2022-12-07T12:30:00Z",
}
ENTITY = """<xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">
0000000001</xbrli:identifier></xbrli:entity>"""


def context(name, dates):
    return (
        f'<xbrli:context id="{name}">{ENTITY}<xbrli:period>{dates}</xbrli:period></xbrli:context>'
    )


def point(name, date):
    return context(name, f"<xbrli:instant>{date}</xbrli:instant>")


def filing():
    registration = context(
        "quarter",
        "<xbrli:startdate>2022-08-01</xbrli:startdate><xbrli:enddate>2022-10-30</xbrli:enddate>",
    )
    return f"""<html xmlns:dei="http://xbrl.sec.gov/dei/2022"
        xmlns:us-gaap="http://fasb.org/us-gaap/2022" xmlns:xbrli="http://www.xbrl.org/2003/instance"
        xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
        xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"><body>
    <ix:header><ix:resources>
      {registration}{point("outstanding", "2022-12-01")}{point("current", "2022-10-30")}
      {point("prior", "2022-07-31")}
      <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
      <xbrli:unit id="par"><xbrli:divide>
        <xbrli:unitnumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitnumerator>
        <xbrli:unitdenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitdenominator>
      </xbrli:divide></xbrli:unit>
    </ix:resources></ix:header>
    <table><tr>
        <td><ix:nonnumeric name="dei:Security12bTitle" contextref="quarter">Capital Stock, par value $.05</ix:nonnumeric></td>
        <td><ix:nonnumeric name="dei:TradingSymbol" contextref="quarter">SYN</ix:nonnumeric></td>
        <td><ix:nonnumeric name="dei:SecurityExchangeName" contextref="quarter">NYSE</ix:nonnumeric></td>
    </tr></table>
    <p>There were <ix:nonfraction name="dei:EntityCommonStockSharesOutstanding" contextref="outstanding"
        unitref="shares" scale="0" decimals="0" format="ixt:num-dot-decimal">12,345</ix:nonfraction>
        shares of capital stock outstanding as of December 1, 2022.</p>
    <table><tr><td>Capital stock, $ <ix:nonfraction name="us-gaap:CommonStockParOrStatedValuePerShare"
        contextref="current" scale="0" decimals="2" unitref="par"><ix:nonfraction
        name="us-gaap:CommonStockParOrStatedValuePerShare" contextref="prior" scale="0" decimals="2"
        unitref="par">.05</ix:nonfraction></ix:nonfraction> par value, 123 issued.</td></tr></table>
    </body></html>"""


def qualify(raw, cutoff=CUTOFF):
    payload = raw.encode()
    periodic = periodic_anchor(payload, METADATA, cik=METADATA["cik"], symbol="SYN", cutoff=cutoff)
    return qualify_tagged_capital_stock(payload, periodic, cutoff=cutoff)


def test_qualified_evidence_binds_same_issuer_filing_and_dates():
    proof = qualify(filing())
    assert proof["registered_par_value_usd"] == "0.05"
    assert proof["common_stock_par_fact_dates"] == ["2022-07-31", "2022-10-30"]
    assert proof["common_shares_count_date"] == "2022-12-01"
    assert proof["filing_sha256"] == stage.digest(filing().encode())


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("xmlns:dei=", "xmlns:issuer-dei="),
        ("$.05", "$.06"),
        ("Capital Stock, par value", "Class A Capital Stock, par value"),
        ("shares of capital stock outstanding", "shares of preferred stock outstanding"),
        ("December 1, 2022.", "December 2, 2022."),
        ("2022-12-01", "2022-12-21"),
        ('contextref="outstanding"', 'contextref="missing"'),
        ('unitref="shares" scale="0"', 'unitref="shares" scale="3"'),
        ('unitref="shares" scale="0"', 'unitref="shares" sign="-" scale="0"'),
        ("iso4217:USD", "iso4217:CAD"),
        ('contextref="prior"', 'contextref="current"'),
        ("xbrli:instant>2022-10-30", "xbrli:instant>2022-10-31"),
        ("0000000001</xbrli:identifier>", "0000000002</xbrli:identifier>"),
        ("</xbrli:entity>", "<xbrli:segment></xbrli:segment></xbrli:entity>"),
        ("</ix:resources>", point("current", "2022-10-30") + "</ix:resources>"),
        (
            "</body>",
            '<ix:nonnumeric name="dei:Security12bTitle">Other Stock</ix:nonnumeric></body>',
        ),
        ("CommonStockParOrStatedValuePerShare", "PreferredStockParOrStatedValuePerShare"),
    ],
)
def test_unbound_or_conflicting_facts_are_refused(old, new):
    assert old in filing()
    with pytest.raises(IdentityEvidenceError):
        qualify(filing().replace(old, new))


def test_unavailable_filing_and_substituted_bytes_are_refused():
    with pytest.raises(IdentityEvidenceError, match="filing_not_available"):
        qualify(filing(), datetime.fromisoformat("2022-12-10T00:00:00+00:00"))
    raw = filing().encode()
    periodic = periodic_anchor(raw, METADATA, cik=METADATA["cik"], symbol="SYN", cutoff=CUTOFF)
    with pytest.raises(IdentityEvidenceError, match="unbound"):
        qualify_tagged_capital_stock(raw + b" ", periodic, cutoff=CUTOFF)


@pytest.mark.parametrize("keys", [[], ["a", "a"], ["outside"], [False]])
def test_plan_requires_distinct_eligible_ownership_gaps(keys):
    plan = {"schema_version": 1, "scope": stage.PLAN_SCOPE, "snapshot_member_keys": keys}
    with pytest.raises(IdentityEvidenceError):
        stage.member_keys(canonical_bytes(plan), {"a", "b"})


def test_checkpoint_replays_stage_with_all_parent_sources(tmp_path, monkeypatch):
    from argparse import Namespace

    from scripts import audit_historical_checkpoint as audit

    directory = tmp_path / "identities"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(audit, "ARTIFACT_ROOT", tmp_path)
    inputs = Namespace(
        directory=directory,
        governing_sources=tmp_path / "governing",
        proxy_sources=tmp_path / "proxies",
        redesignation_sources=tmp_path / "redesignation",
        bank_sources=tmp_path / "banks",
        bank_ownership_sources=tmp_path / "owners",
        capital_sources=tmp_path / "explicit-capital",
        source_capture=tmp_path / "roster",
        retained_checkpoint=tmp_path / "checkpoint",
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
    result, digest, _ = audit.replay_identity(inputs)
    assert result == stored
    assert digest == audit.digest(raw)
    assert calls == [
        (
            directory,
            "baseline.json",
            "plan.json",
            *(getattr(inputs, name.replace("-", "_")) for name in stage.SOURCE_ARGS),
        )
    ]

    monkeypatch.setattr(stage, "recompute", lambda *args: stored | {"altered": True})
    with pytest.raises(ValueError, match="identity_replay_mismatch"):
        audit.replay_identity(inputs)
    inputs.capital_sources = None
    with pytest.raises(ValueError, match="missing_capital_sources"):
        audit.replay_identity(inputs)
