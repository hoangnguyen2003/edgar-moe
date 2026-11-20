from argparse import Namespace

import pytest

from edgar_moe.data.historical_identity import IdentityEvidenceError, instant
from edgar_moe.data.historical_vote_identity import issuer_meeting, voter
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import audit_historical_checkpoint as audit
from scripts import enrich_historical_vote_identity as stage

CUTOFF = instant("2022-12-28T23:23:50Z")
NAME = "Synthetic PLC"
ISSUER = {"accession": "0000000001-22-000001", "filer_cik": "0000000001"}
FIRST = {"accession": "0000000002-22-000001", "filer_cik": "0000000002"}
SECOND = {"accession": "0000000003-22-000001", "filer_cik": "0000000003"}
PERIODIC = {
    "issuer_cik": "0000000001",
    "security_title": "Ordinary Shares, nominal value $0.01 per share",
    "symbol_at_cutoff": "SYN",
    "exchange_at_filing": "NASDAQ Global Select Market",
}
REPORT = """Securities registered pursuant to Section 12(b) of the Act:
Title of each class Trading Symbol(s) Name of each exchange on which registered
Ordinary Shares, nominal value $0.01 per share SYN NASDAQ Global Select Market
Item 5.07
On June 8, 2022, Synthetic Public Limited Company (the “Company”) held its
2022 Annual General Meeting of Shareholders.
Ordinary shares outstanding and entitled to vote as of April 11, 2022, the record date."""
VOTES_WITH_RECORD = """Synthetic Public Limited Company Ticker: SYN Security ID: 123456782
Meeting Date: JUN 08, 2022 Meeting Type: Annual Record Date: APR 11, 2022"""
VOTES_WITH_ISIN = """Synthetic PLC Agenda Number: 777 ------
Security: 123456782 Meeting Type: Annual Meeting Date: 08-Jun-2022 Ticker: SYN ISIN: US1234567824"""


def submission(ref, form, day, body):
    return f"""<SEC-HEADER>
<ACCEPTANCE-DATETIME>{day}123000
ACCESSION NUMBER: {ref["accession"]}
CONFORMED SUBMISSION TYPE: {form}
FILED AS OF DATE: {day}
FILER:
    CENTRAL INDEX KEY: {ref["filer_cik"]}
</SEC-HEADER>
<DOCUMENT>
<TYPE>{form}
<TEXT><html><meta charset="UTF-8"><p>{body}</p></html></TEXT></DOCUMENT>
""".encode()


def meeting(report=REPORT):
    return issuer_meeting(
        submission(ISSUER, "8-K", "20220613", report),
        ISSUER,
        PERIODIC,
        issuer_name=NAME,
        cutoff=CUTOFF,
    )


def test_issuer_and_two_filed_votes_report_the_same_meeting_and_security():
    proof = meeting()
    first = voter(
        submission(FIRST, "N-PX", "20220830", VOTES_WITH_RECORD),
        FIRST,
        proof,
        issuer_name=NAME,
        cutoff=CUTOFF,
        require_record_date=True,
    )
    second = voter(
        submission(SECOND, "N-PX", "20220831", VOTES_WITH_ISIN),
        SECOND,
        proof,
        issuer_name=NAME,
        cutoff=CUTOFF,
        require_record_date=False,
    )
    assert first["cusip"] == second["cusip"] == "123456782"
    assert second["isin"] == "US1234567824"
    assert proof["record_date"] == "2022-04-11"
    assert proof["meeting_date"] == first["meeting_date"] == second["meeting_date"] == "2022-06-08"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("$0.01", "$0.02"),
        ("SYN NASDAQ", "OLD NASDAQ"),
        ("Company”) held", "Company”) cancelled"),
        ("April 11", "June 11"),
        ("Ordinary shares outstanding", "Preferred shares outstanding"),
    ],
)
def test_issuer_requires_registered_class_and_valid_vote_dates(old, new):
    with pytest.raises(IdentityEvidenceError):
        meeting(REPORT.replace(old, new))


@pytest.mark.parametrize(
    ("text", "old", "new", "record"),
    [
        (VOTES_WITH_RECORD, "Public Limited Company", "Other PLC", True),
        (VOTES_WITH_RECORD, "Ticker: SYN", "Ticker: OLD", True),
        (VOTES_WITH_RECORD, "APR 11", "APR 12", True),
        (VOTES_WITH_RECORD, "Annual", "Special", True),
        (VOTES_WITH_RECORD, "123456782", "123456781", True),
        (VOTES_WITH_ISIN, "08-Jun", "09-Jun", False),
        (VOTES_WITH_ISIN, "US1234567824", "US1234567825", False),
    ],
)
def test_voters_require_matching_subject_security_and_meeting(text, old, new, record):
    with pytest.raises(IdentityEvidenceError):
        voter(
            submission(FIRST, "N-PX", "20220830", text.replace(old, new)),
            FIRST,
            meeting(),
            issuer_name=NAME,
            cutoff=CUTOFF,
            require_record_date=record,
        )


def test_post_cutoff_and_repeated_filers_are_rejected():
    with pytest.raises(IdentityEvidenceError, match="unavailable"):
        voter(
            submission(FIRST, "N-PX", "20221227", VOTES_WITH_RECORD),
            FIRST,
            meeting(),
            issuer_name=NAME,
            cutoff=CUTOFF,
            require_record_date=True,
        )
    plan = {
        "schema_version": 1,
        "scope": stage.PLAN_SCOPE,
        "snapshot_member_key": "a" * 64,
        "issuer": ISSUER,
        "record_date_voter": FIRST,
        "second_voter": FIRST,
    }
    with pytest.raises(IdentityEvidenceError, match="non_independent"):
        stage.reference(canonical_bytes(plan), {"a" * 64})


def test_checkpoint_requires_current_vote_evidence_and_an_exact_replay(tmp_path, monkeypatch):
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
    inputs.vote_identity_sources = None
    with pytest.raises(ValueError, match="missing_vote_identity_sources"):
        audit.replay_identity(inputs)
