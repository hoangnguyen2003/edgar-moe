import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from edgar_moe.data.historical_identity import IdentityEvidenceError
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import enrich_historical_class_redesignation as supplement


def plan():
    return {
        "schema_version": 1,
        "scope": "private_one_for_one_class_redesignation_only",
        "references": [
            {
                "snapshot_member_key": "b" * 64,
                "submission_sha256": "e" * 64,
                "primary_filename": "primary.htm",
            }
        ],
    }


@pytest.fixture
def replay(tmp_path, monkeypatch):
    # Isolate the new orchestration from the separately tested parent replay and
    # parser. SourceCache, receipts, content addressing and CLI replay stay real.
    directory = tmp_path / "identities"
    proxies = tmp_path / "proxies"
    directory.mkdir(mode=0o700)
    proxies.mkdir(mode=0o700)
    monkeypatch.setattr(supplement, "ARTIFACT_ROOT", tmp_path)
    base = {
        "kind": "private_issuer_proxy_definition_supplement",
        "baseline_filename": "parent.json",
        "reference_plan_filename": "parent-plan.json",
        "report": {"source_count": 3},
        "binding": {
            "cutoff": "2022-12-28T00:00:00Z",
            "members": [
                {"snapshot_member_key": key * 64, "cik": "0000000001", "symbol_at_snapshot": "SYN"}
                for key in "abc"
            ],
        },
        "results": [
            {
                "snapshot_member_key": "a" * 64,
                "status": "pre_cutoff_SEC_identity_anchored",
                "anchor": {"opaque": "preserve"},
            },
            {"snapshot_member_key": "b" * 64, "status": "ownership_identity_unresolved"},
            {"snapshot_member_key": "c" * 64, "status": "periodic_anchor_unresolved"},
        ],
    }
    raw = canonical_bytes(base) + b"\n"
    baseline_name = "proxy-definition-" + supplement.digest(raw) + ".json"
    (directory / baseline_name).write_bytes(raw)
    monkeypatch.setattr(supplement.previous, "recompute", lambda *a: deepcopy(base))
    own = {
        "cik": "0000000001",
        "accessionNumber": "0000000002-22-000001",
        "form": "SC 13G/A",
        "primaryDocument": "own.htm",
    }
    periodic = {
        "cik": "0000000001",
        "accessionNumber": "0000000001-22-000003",
        "form": "10-Q",
        "primaryDocument": "periodic.htm",
    }
    rows = [own, periodic]
    monkeypatch.setattr(supplement, "submissions", lambda *a: rows)
    monkeypatch.setattr(supplement, "choose_filings", lambda *a: [periodic])
    monkeypatch.setattr(
        supplement, "RetainedCheckpoint", lambda *a: SimpleNamespace(filing=lambda r: b"periodic")
    )
    monkeypatch.setattr(
        supplement,
        "periodic_anchor",
        lambda *a, **kw: {
            "issuer_cik": "0000000001",
            "accession": "0000000001-22-000003",
            "security_title": "Common Stock, par value $.01",
            "available_by": "2022-11-15T00:00:00Z",
        },
    )
    monkeypatch.setattr(supplement, "ownership_candidates", lambda *a: [own])
    monkeypatch.setattr(supplement, "_ownership_subject_name", lambda *a: (b"Example Inc.", {}))
    monkeypatch.setattr(
        supplement,
        "ownership_identity",
        lambda *a, **kw: {
            "cusip": "123456782",
            "accession": own["accessionNumber"],
            "document_sha256": "d" * 64,
            "accepted_at": "2022-02-09T00:00:00Z",
        },
    )
    monkeypatch.setattr(supplement, "qualify_redesignated_class", lambda p, o, e: o)
    monkeypatch.setattr(
        supplement,
        "redesignation_reference",
        lambda *a, **kw: {"accession": "0000000001-12-000001"},
    )
    monkeypatch.setattr(
        supplement,
        "redesignation_evidence",
        lambda *a, **kw: {
            "issuer_name_sha256": supplement.digest(
                supplement.legal_name_key("Example Inc.").encode()
            )
        },
    )
    monkeypatch.setattr(supplement, "join_identity", lambda p, o: {"periodic": p, "ownership": o})

    def source(folder, url, payload):
        key = supplement.digest(url.encode())
        (folder / (key + ".body")).write_bytes(payload)
        (folder / (key + ".json")).write_bytes(
            canonical_bytes(
                {
                    "url": url,
                    "sha256": supplement.digest(payload),
                    "bytes": len(payload),
                    "retrieved_at": "2026-11-16T00:00:00Z",
                }
            )
        )
        return folder / (key + ".body")

    source(
        directory,
        supplement.filing_url(own | {"primaryDocument": own["accessionNumber"] + ".txt"}),
        b"ownership",
    )
    reference = plan()
    reference["references"][0]["submission_sha256"] = supplement.digest(b"proxy")
    proxy_body = source(
        proxies,
        supplement.filing_url(
            {
                "cik": "0000000001",
                "accessionNumber": "0000000001-12-000001",
                "primaryDocument": "0000000001-12-000001.txt",
            }
        ),
        b"proxy",
    )
    (directory / "plan.json").write_bytes(canonical_bytes(reference))
    for folder in (directory, proxies):
        for path in folder.iterdir():
            path.chmod(0o600)
    args = (
        directory,
        baseline_name,
        "plan.json",
        tmp_path / "governing",
        proxies,
        proxies,
        tmp_path / "roster",
        tmp_path / "checkpoint",
    )
    return SimpleNamespace(
        args=args, base=base, directory=directory, proxy_body=proxy_body, rows=rows
    )


def test_replay_preserves_previous_anchors_binding_order_and_all_completion_holds(replay):
    before = deepcopy(replay.base)
    result = supplement.recompute(*replay.args)
    assert replay.base == before
    assert result["binding"] == before["binding"]
    assert len(result["results"]) == len(before["results"])
    assert result["results"][0] == before["results"][0]
    assert result["results"][2] == before["results"][2]
    assert result["report"]["anchors_added"] == 1
    assert result["report"]["baseline_anchor_payloads_changed"] == 0
    assert result["report"]["provider_requests"] == 0
    reference = result["results"][1]["anchor"]["current_annual_redesignation_reference"]
    assert reference["annual_registration"]["accession"] == "0000000001-22-000003"
    assert reference["incorporated_charter"]["accession"] == "0000000001-12-000001"
    for key in (
        "issue_complete",
        "historical_market_continuity_verified",
        "checkpoint_dataset_binding_verified",
        "publication_gate_released",
        "paid_resources",
    ):
        assert result["report"][key] is False


def test_baseline_byte_tampering_fails_before_parent_replay(replay):
    (replay.directory / replay.args[1]).write_bytes(b"{}")
    with pytest.raises(IdentityEvidenceError, match="baseline_digest_mismatch"):
        supplement.recompute(*replay.args)


def test_parent_replay_must_match_the_original_full_payload(replay, monkeypatch):
    monkeypatch.setattr(
        supplement.previous, "recompute", lambda *a: replay.base | {"injected": True}
    )
    with pytest.raises(IdentityEvidenceError, match="baseline_replay_mismatch"):
        supplement.recompute(*replay.args)


def test_algorithm_changes_during_replay_prevent_a_new_result(replay, monkeypatch):
    versions = iter([{"test": "before"}, {"test": "after"}])
    monkeypatch.setattr(supplement, "algorithms", lambda: next(versions))
    with pytest.raises(IdentityEvidenceError, match="algorithm_changed_during_replay"):
        supplement.recompute(*replay.args)


def test_changed_proxy_bytes_cannot_reuse_a_source_receipt(replay):
    replay.proxy_body.write_bytes(b"changed")
    with pytest.raises(IdentityEvidenceError, match="SEC_source_cache_tampered"):
        supplement.recompute(*replay.args)


def test_valid_receipt_cannot_override_the_planned_source_hash(replay):
    reference = plan()
    (replay.directory / "plan.json").write_bytes(canonical_bytes(reference))
    with pytest.raises(IdentityEvidenceError, match="source_proposal_digest_mismatch"):
        supplement.recompute(*replay.args)


def test_missing_periodic_anchor_cannot_be_repaired_with_an_ownership_proxy(replay, monkeypatch):
    monkeypatch.setattr(supplement, "choose_filings", lambda *a: [])
    with pytest.raises(
        IdentityEvidenceError, match="unsupported_redesignation_periodic_registration"
    ):
        supplement.recompute(*replay.args)


def test_empty_ownership_evidence_is_refused(replay, monkeypatch):
    monkeypatch.setattr(supplement, "ownership_candidates", lambda *a: [])
    with pytest.raises(IdentityEvidenceError, match="ownership_identifier_conflict"):
        supplement.recompute(*replay.args)


def test_cli_writes_once_then_replays_and_refuses_tampered_result(replay, monkeypatch, capsys):
    directory, baseline, plan_name, governing, proxies, redesignations, roster, checkpoint = (
        replay.args
    )
    shared = [
        "command",
        "--directory",
        str(directory),
        "--governing-sources",
        str(governing),
        "--proxy-sources",
        str(proxies),
        "--redesignation-sources",
        str(redesignations),
        "--source-capture",
        str(roster),
        "--retained-checkpoint",
        str(checkpoint),
    ]
    monkeypatch.setattr("sys.argv", shared + ["--baseline", baseline, "--references", plan_name])
    assert supplement.main() == 0
    result = next(directory.glob("class-redesignation-*.json"))
    original = result.read_bytes()
    assert supplement.main() == 1  # Exclusive creation preserves the first result.
    assert result.read_bytes() == original
    monkeypatch.setattr("sys.argv", shared + ["--verify", result.name])
    assert supplement.main() == 0
    result.write_bytes(b"{}")
    assert supplement.main() == 1
    assert json.loads(capsys.readouterr().err.splitlines()[-1]) == {
        "refused": "IdentityEvidenceError"
    }


def test_correctly_rehashed_but_falsified_result_cannot_pass_replay(replay, monkeypatch):
    result = supplement.recompute(*replay.args)
    result["report"]["issue_complete"] = True
    raw = canonical_bytes(result) + b"\n"
    name = "class-redesignation-" + supplement.digest(raw) + ".json"
    path = replay.directory / name
    path.write_bytes(raw)
    path.chmod(0o600)
    directory, _, _, governing, proxies, redesignations, roster, checkpoint = replay.args
    monkeypatch.setattr(
        "sys.argv",
        [
            "command",
            "--directory",
            str(directory),
            "--governing-sources",
            str(governing),
            "--proxy-sources",
            str(proxies),
            "--redesignation-sources",
            str(redesignations),
            "--source-capture",
            str(roster),
            "--retained-checkpoint",
            str(checkpoint),
            "--verify",
            name,
        ],
    )
    assert supplement.main() == 1


@pytest.mark.parametrize("key", ["a", "c", "d"])
def test_plan_cannot_override_an_existing_anchor_or_unqualified_member(replay, key):
    value = plan()
    value["references"][0]["snapshot_member_key"] = key * 64
    with pytest.raises(IdentityEvidenceError, match="invalid_or_duplicate"):
        supplement.references(canonical_bytes(value), {"b" * 64})


def test_duplicate_proposal_is_not_counted_twice():
    value = plan()
    value["references"] *= 2
    with pytest.raises(IdentityEvidenceError, match="invalid_or_duplicate"):
        supplement.references(canonical_bytes(value), {"b" * 64})


def test_retained_document_integrity_failure_is_not_swallowed_as_a_parser_refusal(
    replay, monkeypatch
):
    def fail(_row):
        raise IdentityEvidenceError("retained_source_integrity_failed")

    monkeypatch.setattr(supplement, "RetainedCheckpoint", lambda *a: SimpleNamespace(filing=fail))
    with pytest.raises(IdentityEvidenceError, match="retained_source_integrity_failed"):
        supplement.recompute(*replay.args)


def test_two_different_identifiers_cannot_be_selected_by_recency(replay, monkeypatch):
    monkeypatch.setattr(supplement, "ownership_candidates", lambda *a: [replay.rows[0]] * 2)
    items = iter(["123456782", "111111118"])
    monkeypatch.setattr(supplement, "ownership_identity", lambda *a, **kw: {"cusip": next(items)})
    with pytest.raises(IdentityEvidenceError, match="ownership_identifier_conflict"):
        supplement.recompute(*replay.args)


def test_subject_mismatch_is_refused_even_with_same_document_class(replay, monkeypatch):
    monkeypatch.setattr(supplement, "_ownership_subject_name", lambda *a: (b"Another Inc.", {}))
    with pytest.raises(IdentityEvidenceError, match="subject_name_conflict"):
        supplement.recompute(*replay.args)
