import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from edgar_moe.data.historical_holdings_class import (
    FORM_NS,
    TABLE_NS,
    filed_nonvoting_class,
    qualify_nonvoting_ownership,
)
from edgar_moe.data.historical_identity import IdentityEvidenceError
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import enrich_historical_identity_class as supplement

MANAGER = "0000000002"
ISSUER = "0000000001"
CUSIP = "000000018"
NAME = "Example & Co Inc"
CUTOFF = datetime(2022, 12, 28, tzinfo=UTC)


def row(title="COM NON VTG", *, name="Example &amp; Co Inc", option=""):
    return f"""<infoTable><nameOfIssuer>{name}</nameOfIssuer><titleOfClass>{title}</titleOfClass>
<cusip>{CUSIP}</cusip><value>10</value><shrsOrPrnAmt><sshPrnamt>20</sshPrnamt>
<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>{option}
<votingAuthority><Sole>20</Sole><Shared>0</Shared><None>0</None></votingAuthority></infoTable>"""


def source(*, quarter=3, rows=None):
    month, filed = ("09-30-2021", "20211101") if quarter == 3 else ("06-30-2021", "20210801")
    report = "2021-09-30" if quarter == 3 else "2021-06-30"
    accession = f"0000000002-21-00000{quarter}"
    metadata = {
        "accessionNumber": accession,
        "form": "13F-HR",
        "acceptanceDateTime": filed[:4] + "-" + filed[4:6] + "-" + filed[6:] + "T20:00:00Z",
        "filingDate": filed[:4] + "-" + filed[4:6] + "-" + filed[6:],
        "reportDate": report,
        "primaryDocument": "xslForm13F_X01/primary_doc.xml",
    }
    if rows is None:
        rows = [row(), row(), row("COM", option="<putCall>Put</putCall>")]
    payload = f"""<SEC-HEADER>source.hdr.sgml
<ACCEPTANCE-DATETIME>{filed}160000
ACCESSION NUMBER: {accession}
CONFORMED SUBMISSION TYPE: 13F-HR
CONFORMED PERIOD OF REPORT: {report.replace("-", "")}
FILED AS OF DATE: {filed}
FILER:
    COMPANY DATA:
        COMPANY CONFORMED NAME: Separate Manager Inc
        CENTRAL INDEX KEY: {MANAGER}
</SEC-HEADER>
<DOCUMENT>
<TYPE>13F-HR
<FILENAME>primary_doc.xml
<TEXT><XML><edgarSubmission xmlns="{FORM_NS}">
<headerData><submissionType>13F-HR</submissionType><filerInfo><liveTestFlag>LIVE</liveTestFlag>
<filer><credentials><cik>{MANAGER}</cik></credentials></filer><periodOfReport>{month}</periodOfReport>
</filerInfo></headerData><formData><coverPage><reportCalendarOrQuarter>{month}</reportCalendarOrQuarter>
<isAmendment>false</isAmendment><reportType>13F HOLDINGS REPORT</reportType></coverPage>
<summaryPage><tableEntryTotal>{len(rows)}</tableEntryTotal><isConfidentialOmitted>false</isConfidentialOmitted>
</summaryPage></formData></edgarSubmission></XML></TEXT>
</DOCUMENT>
<DOCUMENT>
<TYPE>INFORMATION TABLE
<FILENAME>table.xml
<TEXT><XML><informationTable xmlns="{TABLE_NS}">{"".join(rows)}</informationTable></XML></TEXT>
</DOCUMENT>""".encode()
    return payload, {
        "manager_cik": MANAGER,
        "metadata": metadata,
        "information_filename": "table.xml",
        "document_sha256": hashlib.sha256(payload).hexdigest(),
    }


def extract(payload=None, reference=None, **changes):
    default, ref = source()
    return filed_nonvoting_class(
        default if payload is None else payload,
        ref if reference is None else reference,
        **{"issuer_name": NAME, "cusip": CUSIP, "cutoff": CUTOFF, **changes},
    )


def test_exact_issuer_name_identifier_and_class_are_separate_from_manager_and_voting_counts():
    proof = extract()
    assert proof["manager_cik"] == MANAGER != ISSUER
    assert proof["manager_CIK_used_as_issuer_identity"] is False
    assert len(proof["matching_row_sha256s"]) == 2
    assert proof["derivative_rows_excluded"] == 1
    assert proof["available_by"] == "2021-11-15T00:00:00+00:00"
    assert "accepted_at" not in proof
    assert proof["class_signature"] == ["common", "", "nonvoting"]


@pytest.mark.parametrize(
    "before,after",
    [
        ("CENTRAL INDEX KEY: " + MANAGER, "CENTRAL INDEX KEY: " + ISSUER),
        ("CONFORMED SUBMISSION TYPE: 13F-HR", "CONFORMED SUBMISSION TYPE: 13F-HR/A"),
        ("ACCESSION NUMBER: 0000000002-21-000003", "ACCESSION NUMBER: 0000000002-21-000004"),
        ("20211101160000", "20211102160000"),
        ("<cik>" + MANAGER, "<cik>" + ISSUER),
        ("<liveTestFlag>LIVE", "<liveTestFlag>TEST"),
        ("<tableEntryTotal>3", "<tableEntryTotal>2"),
        ("<isAmendment>false", "<isAmendment>true"),
        ("<isConfidentialOmitted>false", "<isConfidentialOmitted>true"),
        ("<FILENAME>table.xml", "<FILENAME>other.xml"),
        (TABLE_NS, "https://invalid.example/informationtable"),
        ("<XML><informationTable", '<XML><!DOCTYPE x [<!ENTITY x "boom">]><informationTable'),
    ],
)
def test_tampered_header_manager_dates_document_counts_or_XML_are_refused(before, after):
    payload, ref = source()
    assert before.encode() in payload
    payload = payload.replace(before.encode(), after.encode())
    ref["document_sha256"] = hashlib.sha256(payload).hexdigest()
    with pytest.raises(IdentityEvidenceError):
        extract(payload, ref)


def test_a_conflicting_duplicate_target_row_cannot_hide_behind_matching_rows():
    for bad in [
        row("COM"),
        row("PFD"),
        row(name="Another Company Inc"),
        row().replace("<cusip>", f"<cusip>{CUSIP}</cusip><cusip>"),
    ]:
        payload, ref = source(rows=[row(), bad])
        with pytest.raises(IdentityEvidenceError):
            extract(payload, ref)
    payload, ref = source(rows=[row(option="<putCall>Call</putCall>")])
    with pytest.raises(IdentityEvidenceError, match="missing_holdings"):
        extract(payload, ref)


def test_source_hash_identity_cutoff_and_late_metadata_fail_closed():
    payload, ref = source()
    with pytest.raises(IdentityEvidenceError, match="digest"):
        extract(payload + b" ", ref)
    with pytest.raises(IdentityEvidenceError):
        extract(cutoff=datetime(2021, 11, 14, tzinfo=UTC))
    ref["metadata"]["acceptanceDateTime"] = "2026-01-01T00:00:00Z"
    with pytest.raises(IdentityEvidenceError):
        extract(payload, ref)
    for changes in [{"issuer_name": "Another Inc"}, {"cusip": "000000019"}]:
        with pytest.raises(IdentityEvidenceError):
            extract(**changes)


def proofs():
    return [extract(*source(quarter=q)) for q in (2, 3)]


def test_qualification_needs_two_distinct_pre_ownership_reports_and_preserves_original_class():
    periodic = {"issuer_cik": ISSUER, "security_title": "Common Stock Non-Voting, $0.01 par value"}
    own = {
        "issuer_cik": ISSUER,
        "security_title": "Common Stock",
        "cusip": CUSIP,
        "accepted_at": "2022-02-01T00:00:00Z",
    }
    refs = proofs()
    qualified = qualify_nonvoting_ownership(periodic, own, refs)
    assert qualified["security_title"] == "Non-voting Common Stock"
    assert qualified["reported_security_title"] == own["security_title"]
    for changes in [
        {"issuer_cik": MANAGER},
        {"security_title": "Class A Common Stock"},
        {"security_title": "Ordinary Shares"},
        {"security_title": "Common Stock voting"},
        {"security_title": "Common Stock, $0.25"},
        {"accepted_at": "2021-10-01T00:00:00Z"},
    ]:
        with pytest.raises(IdentityEvidenceError):
            qualify_nonvoting_ownership(periodic, {**own, **changes}, refs)
    with pytest.raises(IdentityEvidenceError):
        qualify_nonvoting_ownership(periodic, own, [refs[0], refs[0]])
    assert own["security_title"] == "Common Stock"


def plan_fixture():
    pairs = [source(quarter=q) for q in (2, 3)]
    rows = [ref["metadata"] for _, ref in pairs]
    index = {
        "cik": MANAGER,
        "filings": {"files": [], "recent": {k: [row[k] for row in rows] for k in rows[0]}},
    }
    body = canonical_bytes(index)
    plan = {
        "schema_version": 1,
        "scope": "private_identity_corroboration_only",
        "manager_cik": MANAGER,
        "manager_index_sha256": hashlib.sha256(body).hexdigest(),
        "references": [ref for _, ref in pairs],
    }

    class Cache:
        def get(self, url):
            if url.endswith(".json"):
                return body
            return next(raw for raw, ref in pairs if ref["metadata"]["accessionNumber"] in url)

    return plan, index, Cache


def test_reference_plan_checks_manager_history_and_rejects_unread_archives_amendments_or_changed_sources():
    plan, index, Cache = plan_fixture()
    assert len(supplement.reference_plan(canonical_bytes(plan), Cache(), CUTOFF)) == 2
    for mode in ["index_hash", "archive", "amendment", "duplicate_period", "source_hash"]:
        p, i = deepcopy(plan), deepcopy(index)
        if mode == "index_hash":
            p["manager_index_sha256"] = "0" * 64
        if mode == "archive":
            i["filings"]["files"] = [{"filingTo": "2022-01-01"}]
        if mode == "amendment":
            for key, values in i["filings"]["recent"].items():
                values.append("13F-HR/A" if key == "form" else values[0])
        if mode == "duplicate_period":
            p["references"] = [p["references"][0]] * 2
        if mode == "source_hash":
            p["references"][0]["document_sha256"] = "0" * 64
        body = canonical_bytes(i)
        if mode != "index_hash":
            p["manager_index_sha256"] = hashlib.sha256(body).hexdigest()

        class BadCache(Cache):
            def __init__(self, index_body):
                self.index_body = index_body

            def get(self, url):
                return self.index_body if url.endswith(".json") else super().get(url)

        with pytest.raises(IdentityEvidenceError):
            supplement.reference_plan(canonical_bytes(p), BadCache(body), CUTOFF)


def test_supplement_preserves_existing_anchor_and_every_unresolved_candidate(monkeypatch, tmp_path):
    tmp_path.chmod(0o700)
    baseline = {
        "binding": {
            "cutoff": "2022-12-28T00:00:00Z",
            "members": [{"snapshot_member_key": key} for key in ["kept", "new", "unresolved"]],
        },
        "report": {"source_count": 3},
        "results": [
            {
                "snapshot_member_key": "kept",
                "status": "pre_cutoff_SEC_identity_anchored",
                "anchor": {"original": True},
            },
            {"snapshot_member_key": "new", "status": "ownership_identity_unresolved"},
            {"snapshot_member_key": "unresolved", "status": "periodic_anchor_unresolved"},
        ],
    }
    for name, body in [("baseline.json", canonical_bytes(baseline)), ("plan.json", b"{}")]:
        p = tmp_path / name
        p.write_bytes(body)
        p.chmod(0o600)
    monkeypatch.setattr(supplement, "ARTIFACT_ROOT", tmp_path.parent)
    monkeypatch.setattr(
        supplement.baseline_verifier,
        "replay",
        lambda *a: {"status": "offline_source_replay_verified"},
    )
    monkeypatch.setattr(supplement, "reference_plan", lambda *a: [])
    monkeypatch.setattr(supplement, "RetainedCheckpoint", lambda *a: None)

    def candidate(member, *a):
        assert member["snapshot_member_key"] == "new"
        return {"new": True}

    monkeypatch.setattr(supplement, "candidate_supplement", candidate)
    result = supplement.recompute(tmp_path, "baseline.json", "plan.json", tmp_path, tmp_path)
    assert result["results"][0] == baseline["results"][0]
    assert result["results"][2] == baseline["results"][2]
    assert result["binding"] == baseline["binding"]
    assert result["report"]["anchors_added"] == 1
    assert result["report"]["source_count"] == 3
    assert result["report"]["issue_complete"] is False
    assert (tmp_path / "baseline.json").read_bytes() == canonical_bytes(baseline)


@pytest.mark.parametrize("mode", ["digest", "recomputed_result"])
def test_CLI_replay_rejects_modified_or_nonreproducible_supplements(
    monkeypatch, tmp_path, capsys, mode
):
    tmp_path.chmod(0o700)
    value = {
        "baseline_filename": "baseline.json",
        "reference_plan_filename": "plan.json",
        "results": ["modified"],
    }
    raw = canonical_bytes(value) + b"\n"
    name = "class-enrichment-" + hashlib.sha256(raw).hexdigest() + ".json"
    p = tmp_path / name
    p.write_bytes(raw + (b" " if mode == "digest" else b""))
    p.chmod(0o600)
    monkeypatch.setattr(supplement, "ARTIFACT_ROOT", tmp_path.parent)
    monkeypatch.setattr(supplement, "recompute", lambda *a: {**value, "results": ["original"]})
    monkeypatch.setattr(
        "sys.argv",
        [
            "enrich",
            "--directory",
            str(tmp_path),
            "--verify",
            name,
            "--source-capture",
            str(tmp_path),
            "--retained-checkpoint",
            str(tmp_path),
        ],
    )
    assert supplement.main() == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err) == {"refused": "IdentityEvidenceError"}


def test_candidate_does_not_promote_reporting_manager_or_wrong_subject_CIK(monkeypatch):
    periodic = {"issuer_cik": ISSUER, "security_title": "Common Stock Non-Voting, $0.01"}
    metadata = {
        "cik": ISSUER,
        "accessionNumber": "0000000001-22-000001",
        "primaryDocument": "primary.htm",
    }

    class Retained:
        def filing(self, _row):
            return b"retained periodic bytes"

    class Cache:
        def get(self, _url):
            return f"""<SEC-HEADER>header
ACCESSION NUMBER: {metadata["accessionNumber"]}
SUBJECT COMPANY:
    COMPANY DATA:
        COMPANY CONFORMED NAME: Another Company
        CENTRAL INDEX KEY: {MANAGER}
</SEC-HEADER>""".encode()

    monkeypatch.setattr(supplement, "submissions", lambda *a: [metadata])
    monkeypatch.setattr(supplement, "choose_filings", lambda *a: [metadata])
    monkeypatch.setattr(supplement, "periodic_anchor", lambda *a, **k: periodic)
    monkeypatch.setattr(supplement, "ownership_candidates", lambda *a: [metadata])
    assert (
        supplement.candidate_supplement(
            {"cik": ISSUER, "symbol_at_snapshot": "OLD"}, [], Cache(), Retained(), CUTOFF
        )
        is None
    )

    class Corrupt(Cache):
        def get(self, _url):
            raise IdentityEvidenceError("SEC_source_cache_tampered")

    with pytest.raises(IdentityEvidenceError, match="cache_tampered"):
        supplement.candidate_supplement(
            {"cik": ISSUER, "symbol_at_snapshot": "OLD"}, [], Corrupt(), Retained(), CUTOFF
        )
