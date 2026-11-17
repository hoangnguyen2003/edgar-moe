import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from edgar_moe.data.historical_bank_filings import ANCHOR_STATUS
from edgar_moe.data.historical_identity import IdentityEvidenceError
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import enrich_historical_bank_filings as stage


def plan():
    return {
        "schema_version": 1,
        "scope": "private_FDIC_periodic_exact_issuer_proxy_citation_only",
        "references": [
            {
                "snapshot_member_key": "b" * 64,
                "certificate": 12345,
                "proxy_filing_id": 8,
                "ownership_accession": "0000000002-22-000001",
            }
        ],
    }


@pytest.fixture
def replay(tmp_path, monkeypatch):
    folders = [tmp_path / n for n in ["identities", "bank", "owners"]]
    for p in folders:
        p.mkdir(mode=0o700)
    directory, bank, owners = folders
    base = {
        "kind": "private_charter_class_redesignation_supplement",
        "baseline_filename": "parent.json",
        "reference_plan_filename": "parent-plan.json",
        "binding": {
            "cutoff": "2022-12-28T00:00:00Z",
            "members": [
                {"snapshot_member_key": v * 64, "cik": "0000000001", "symbol_at_snapshot": "SYN"}
                for v in "abc"
            ],
        },
        "results": [
            {
                "snapshot_member_key": "a" * 64,
                "status": "pre_cutoff_SEC_identity_anchored",
                "anchor": {"preserve": "all fields"},
            },
            {"snapshot_member_key": "b" * 64, "status": "periodic_anchor_unresolved"},
            {"snapshot_member_key": "c" * 64, "status": "ownership_identity_unresolved"},
        ],
        "report": {"source_count": 3},
    }
    raw = canonical_bytes(base)
    name = "class-redesignation-" + stage.digest(raw) + ".json"
    (directory / name).write_bytes(raw)
    (directory / "plan.json").write_bytes(canonical_bytes(plan()))
    for p in directory.iterdir():
        p.chmod(0o600)
    monkeypatch.setattr(stage, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(stage.previous, "recompute", lambda *a: deepcopy(base))
    monkeypatch.setattr(stage, "RetainedCheckpoint", lambda *a: SimpleNamespace())
    calls = []

    def candidate(member, ref, cache, fdic, owners, retained, cutoff):
        assert cache.client is None and owners.client is None
        assert isinstance(fdic, stage.FDICCache)
        calls.append(member["snapshot_member_key"])
        return {"source_qualified": "synthetic"}

    monkeypatch.setattr(stage, "candidate", candidate)
    args = (
        directory,
        name,
        "plan.json",
        tmp_path / "governing",
        tmp_path / "proxies",
        tmp_path / "redesignations",
        bank,
        owners,
        tmp_path / "roster",
        tmp_path / "checkpoint",
    )
    return SimpleNamespace(args=args, directory=directory, base=base, calls=calls)


def test_only_missing_periodic_evidence_is_supplemented_without_mislabeling_regulators(replay):
    before = deepcopy(replay.base)
    value = stage.recompute(*replay.args)
    assert replay.base == before and value["binding"] == before["binding"]
    assert (
        value["results"][0] == before["results"][0] and value["results"][2] == before["results"][2]
    )
    assert value["results"][1]["status"] == ANCHOR_STATUS
    assert value["report"]["statuses"] == {
        "pre_cutoff_SEC_identity_anchored": 1,
        ANCHOR_STATUS: 1,
        "ownership_identity_unresolved": 1,
    }
    assert value["report"]["anchors_added"] == 1 and replay.calls == ["b" * 64]
    for flag in [
        "issue_complete",
        "publication_gate_released",
        "historical_market_continuity_verified",
        "checkpoint_dataset_binding_verified",
        "paid_resources",
        "subject_tax_identifier_used_as_join_key",
    ]:
        assert value["report"][flag] is False


@pytest.mark.parametrize("member", ["a", "c", "d"])
def test_existing_anchors_other_refusals_and_foreign_members_cannot_be_overridden(member):
    value = plan()
    value["references"][0]["snapshot_member_key"] = member * 64
    with pytest.raises(IdentityEvidenceError):
        stage.references(canonical_bytes(value), {"b" * 64})


@pytest.mark.parametrize(
    "field,value",
    [
        ("certificate", True),
        ("certificate", 0),
        ("proxy_filing_id", False),
        ("ownership_accession", "../bad"),
    ],
)
def test_reference_fields_have_strict_types_and_no_path_aliases(field, value):
    data = plan()
    data["references"][0][field] = value
    with pytest.raises(IdentityEvidenceError):
        stage.references(canonical_bytes(data), {"b" * 64})


def test_duplicate_members_are_not_counted_twice():
    value = plan()
    value["references"] *= 2
    with pytest.raises(IdentityEvidenceError):
        stage.references(canonical_bytes(value), {"b" * 64})


def test_parent_bytes_and_parent_replay_cannot_be_replaced_by_a_count(replay, monkeypatch):
    monkeypatch.setattr(stage.previous, "recompute", lambda *a: replay.base | {"tampered": True})
    with pytest.raises(IdentityEvidenceError, match="parent_replay_mismatch"):
        stage.recompute(*replay.args)
    (replay.directory / replay.args[1]).write_bytes(b"{}")
    with pytest.raises(IdentityEvidenceError, match="baseline_digest_mismatch"):
        stage.recompute(*replay.args)


def test_algorithm_changes_during_replay_refuse_the_output(replay, monkeypatch):
    versions = iter([{"before": "a" * 64}, {"after": "b" * 64}])
    monkeypatch.setattr(stage, "algorithms", lambda: next(versions))
    with pytest.raises(IdentityEvidenceError, match="algorithm_changed"):
        stage.recompute(*replay.args)


def shared(replay):
    args = replay.args
    return [
        "command",
        "--directory",
        str(args[0]),
        "--governing-sources",
        str(args[3]),
        "--proxy-sources",
        str(args[4]),
        "--redesignation-sources",
        str(args[5]),
        "--bank-sources",
        str(args[6]),
        "--bank-ownership-sources",
        str(args[7]),
        "--source-capture",
        str(args[8]),
        "--retained-checkpoint",
        str(args[9]),
    ]


def test_cli_captures_once_then_independently_replays_the_full_payload(replay, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", shared(replay) + ["--baseline", replay.args[1], "--references", "plan.json"]
    )
    assert stage.main() == 0
    result = next(replay.directory.glob("bank-filings-*.json"))
    before = result.read_bytes()
    assert stage.main() == 1 and result.read_bytes() == before
    monkeypatch.setattr("sys.argv", shared(replay) + ["--verify", result.name])
    assert stage.main() == 0
    value = json.loads(before)
    value["report"]["issue_complete"] = True
    raw = canonical_bytes(value) + b"\n"
    name = "bank-filings-" + stage.digest(raw) + ".json"
    p = replay.directory / name
    p.write_bytes(raw)
    p.chmod(0o600)
    monkeypatch.setattr("sys.argv", shared(replay) + ["--verify", name])
    assert stage.main() == 1


def test_a_missing_current_source_never_falls_back_to_parent_anchors(replay, monkeypatch):
    def missing(*a):
        raise FileNotFoundError("missing source")

    monkeypatch.setattr(stage, "candidate", missing)
    with pytest.raises(FileNotFoundError):
        stage.recompute(*replay.args)


def test_append_rejects_existing_anchor_and_wrong_refusal_type(replay):
    for key in ["a", "c", "d"]:
        with pytest.raises(IdentityEvidenceError):
            stage.append_results(replay.base, {key * 64: {}})


@pytest.fixture
def fdic(tmp_path):
    raw = b"public synthetic source"
    url = stage.FDIC_API + "7/attachment/1"
    key = stage.digest(url.encode())
    receipt = {
        "url": url,
        "status_code": 200,
        "TLS_verified": True,
        "automatic_retries": 0,
        "location": None,
        "bytes": len(raw),
        "sha256": stage.digest(raw),
        "retrieved_at": "2026-11-17T00:00:00Z",
    }
    (tmp_path / (key + ".body")).write_bytes(raw)
    (tmp_path / (key + ".json")).write_bytes(canonical_bytes(receipt))
    for p in tmp_path.iterdir():
        p.chmod(0o600)
    return tmp_path, url, key, receipt


def test_retained_FDIC_source_checks_url_receipt_bytes_and_never_opens_a_client(fdic):
    folder, url, key, receipt = fdic
    with stage._capture_directory(folder, root=folder.parent) as fd:
        cache = stage.FDICCache(fd)
        assert cache.get(url) == b"public synthetic source" and len(cache.used) == 1
        with pytest.raises(IdentityEvidenceError, match="unapproved"):
            cache.get("https://example.com/7/attachment/1")
        (folder / (key + ".body")).write_bytes(b"changed")
        with pytest.raises(IdentityEvidenceError, match="tampered"):
            cache.get(url)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status_code", 302),
        ("TLS_verified", False),
        ("location", "https://example.com"),
        ("automatic_retries", 1),
        ("url", "https://example.com"),
    ],
)
def test_redirects_TLS_bypasses_retries_and_mislabeled_sources_are_not_replayed(fdic, field, value):
    folder, url, key, receipt = fdic
    receipt[field] = value
    (folder / (key + ".json")).write_bytes(canonical_bytes(receipt))
    with (
        stage._capture_directory(folder, root=folder.parent) as fd,
        pytest.raises(IdentityEvidenceError),
    ):
        stage.FDICCache(fd).get(url)
