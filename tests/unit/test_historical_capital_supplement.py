import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from edgar_moe.data.historical_identity import IdentityEvidenceError
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import enrich_historical_capital_definition as stage


def plan():
    return {
        "schema_version": 1,
        "scope": "private_explicit_capital_definition_and_exact_ownership_citation_only",
        "references": [
            {
                "snapshot_member_key": "b" * 64,
                "proxy_accession": "0000000001-22-000002",
                "proxy_filename": "proxy.htm",
                "proxy_sha256": "c" * 64,
                "ownership_accession": "0000000002-22-000001",
            }
        ],
    }


@pytest.fixture
def replay(tmp_path, monkeypatch):
    directory, sources = [tmp_path / n for n in ["identity", "capital"]]
    for p in [directory, sources]:
        p.mkdir(mode=0o700)
    base = {
        "kind": "private_FDIC_periodic_SEC_ownership_supplement",
        "baseline_filename": "parent.json",
        "reference_plan_filename": "parent-plan.json",
        "binding": {
            "cutoff": "2022-12-28T00:00:00Z",
            "members": [
                {"snapshot_member_key": v * 64, "cik": "0000000001", "symbol_at_snapshot": "SYN"}
                for v in "abcd"
            ],
        },
        "results": [
            {
                "snapshot_member_key": "a" * 64,
                "status": "pre_cutoff_SEC_identity_anchored",
                "anchor": {"original": "SEC"},
            },
            {"snapshot_member_key": "b" * 64, "status": "ownership_identity_unresolved"},
            {
                "snapshot_member_key": "c" * 64,
                "status": "pre_cutoff_FDIC_SEC_identity_anchored",
                "anchor": {"original": "FDIC"},
            },
            {"snapshot_member_key": "d" * 64, "status": "periodic_anchor_unresolved"},
        ],
        "report": {"source_count": 4},
    }
    raw = canonical_bytes(base)
    name = "bank-filings-" + stage.digest(raw) + ".json"
    (directory / name).write_bytes(raw)
    (directory / "plan.json").write_bytes(canonical_bytes(plan()))
    for p in directory.iterdir():
        p.chmod(0o600)
    monkeypatch.setattr(stage, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(stage.previous, "recompute", lambda *a: deepcopy(base))
    monkeypatch.setattr(stage, "RetainedCheckpoint", lambda *a: SimpleNamespace())
    calls = []

    def candidate(member, ref, cache, source, retained, cutoff):
        assert cache.client is None and source.client is None
        calls.append(member["snapshot_member_key"])
        return {"source_qualified": "synthetic"}

    monkeypatch.setattr(stage, "candidate", candidate)
    args = (
        directory,
        name,
        "plan.json",
        *(tmp_path / n for n in ["governing", "proxies", "redesignation", "banks", "owners"]),
        sources,
        tmp_path / "roster",
        tmp_path / "checkpoint",
    )
    return SimpleNamespace(args=args, directory=directory, base=base, calls=calls)


def test_only_missing_ownership_is_added_preserving_both_regulators_and_all_source_rows(replay):
    before = deepcopy(replay.base)
    result = stage.recompute(*replay.args)
    assert replay.base == before and result["binding"] == before["binding"]
    assert result["results"][0] == before["results"][0]
    assert result["results"][2:] == before["results"][2:]
    assert result["report"]["statuses"] == {
        "pre_cutoff_SEC_identity_anchored": 2,
        "pre_cutoff_FDIC_SEC_identity_anchored": 1,
        "periodic_anchor_unresolved": 1,
    }
    assert replay.calls == ["b" * 64]
    for key in [
        "historical_market_continuity_verified",
        "checkpoint_dataset_binding_verified",
        "publication_gate_released",
        "issue_complete",
    ]:
        assert result["report"][key] is False


@pytest.mark.parametrize("key", ["a", "c", "d", "e"])
def test_existing_anchors_periodic_gaps_and_nonmembers_cannot_be_replaced(key):
    value = plan()
    value["references"][0]["snapshot_member_key"] = key * 64
    with pytest.raises(IdentityEvidenceError):
        stage.references(canonical_bytes(value), {"b" * 64})


@pytest.mark.parametrize(
    "field,value",
    [
        ("proxy_sha256", "not-a-hash"),
        ("proxy_filename", "../proxy.htm"),
        ("proxy_filename", "proxy.html?x"),
        ("ownership_accession", False),
        ("proxy_accession", "bad"),
        ("manual_alias", "common=capital"),
    ],
)
def test_reference_plan_rejects_path_aliases_types_and_manual_correspondences(field, value):
    data = plan()
    data["references"][0][field] = value
    with pytest.raises(IdentityEvidenceError):
        stage.references(canonical_bytes(data), {"b" * 64})


def test_duplicate_and_malformed_plans_cannot_inflate_the_anchor_count():
    data = plan()
    data["references"] *= 2
    with pytest.raises(IdentityEvidenceError):
        stage.references(canonical_bytes(data), {"b" * 64})
    data = plan()
    data["schema_version"] = True
    with pytest.raises(IdentityEvidenceError):
        stage.references(canonical_bytes(data), {"b" * 64})


def test_corrupt_parent_or_a_nonidentical_parent_replay_cannot_fall_back(replay, monkeypatch):
    monkeypatch.setattr(stage.previous, "recompute", lambda *a: replay.base | {"tampered": True})
    with pytest.raises(IdentityEvidenceError, match="parent_replay_mismatch"):
        stage.recompute(*replay.args)
    (replay.directory / replay.args[1]).write_bytes(b"{}")
    with pytest.raises(IdentityEvidenceError, match="baseline_digest_mismatch"):
        stage.recompute(*replay.args)


def test_algorithm_changes_or_missing_current_evidence_refuse(replay, monkeypatch):
    versions = iter([{"before": "a" * 64}, {"after": "b" * 64}])
    monkeypatch.setattr(stage, "algorithms", lambda: next(versions))
    with pytest.raises(IdentityEvidenceError, match="algorithm_changed"):
        stage.recompute(*replay.args)
    monkeypatch.setattr(stage, "algorithms", lambda: {})

    def missing(*args):
        raise FileNotFoundError("missing current proxy")

    monkeypatch.setattr(stage, "candidate", missing)
    with pytest.raises(FileNotFoundError):
        stage.recompute(*replay.args)


def test_exclusive_capture_and_independent_replay_reject_changed_payload(replay, monkeypatch):
    names = [
        "directory",
        "governing-sources",
        "proxy-sources",
        "redesignation-sources",
        "bank-sources",
        "bank-ownership-sources",
        "capital-sources",
        "source-capture",
        "retained-checkpoint",
    ]
    paths = [replay.args[0], *replay.args[3:]]
    argv = ["command"]
    for name, path in zip(names, paths, strict=True):
        argv += ["--" + name, str(path)]
    monkeypatch.setattr(
        "sys.argv", argv + ["--baseline", replay.args[1], "--references", "plan.json"]
    )
    assert stage.main() == 0
    result = next(replay.directory.glob("capital-definition-*.json"))
    before = result.read_bytes()
    assert stage.main() == 1 and result.read_bytes() == before
    monkeypatch.setattr("sys.argv", argv + ["--verify", result.name])
    assert stage.main() == 0
    value = json.loads(before)
    value["report"]["issue_complete"] = True
    raw = canonical_bytes(value) + b"\n"
    name = "capital-definition-" + stage.digest(raw) + ".json"
    p = replay.directory / name
    p.write_bytes(raw)
    p.chmod(0o600)
    monkeypatch.setattr("sys.argv", argv + ["--verify", name])
    assert stage.main() == 1
