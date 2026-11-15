from argparse import Namespace
from copy import deepcopy

import pytest

from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import audit_historical_checkpoint as audit


def members():
    return [
        {"cik": "0000000001", "symbol_at_snapshot": "OLD", "snapshot_member_key": "a" * 64},
        {"cik": "0000000002", "symbol_at_snapshot": "DEL", "snapshot_member_key": "b" * 64},
        {"cik": "0000000003", "symbol_at_snapshot": "CLS.A", "snapshot_member_key": "c" * 64},
        {"cik": "0000000003", "symbol_at_snapshot": "CLS.B", "snapshot_member_key": "d" * 64},
    ]


def requested():
    return [{"cik": row["cik"], "symbol": row["symbol_at_snapshot"]} for row in members()]


def test_shared_issuer_share_classes_keep_their_own_source_rows():
    result = audit.compare_rosters(members(), requested())
    assert result["exact_fixed_source_roster"] is True
    assert result["source_candidates"] == 4
    assert result["source_issuers"] == 3
    assert result["source_additional_share_class_rows"] == 1
    assert result["literal_issuer_symbol_matches"] == 4
    assert result["source_pair_set_sha256"] == result["requested_pair_set_sha256"]


def test_current_rename_and_later_delisting_do_not_replace_historical_members():
    current = requested()
    current[0]["symbol"] = "NEW"
    current.pop(1)
    before = deepcopy(members())
    result = audit.compare_rosters(members(), current)
    assert result["exact_fixed_source_roster"] is False
    assert result["source_securities_absent_from_checkpoint"] == 2
    assert result["checkpoint_securities_outside_fixed_source"] == 1
    assert result["literal_issuer_symbol_matches"] == 2
    assert result["issuer_only_overlap"] == 2
    assert result["renames_or_class_aliases_inferred"] is False
    assert members() == before


def test_different_security_of_same_issuer_does_not_match_by_cik_alone():
    rows = requested()
    rows[-1]["symbol"] = "CLS.C"
    result = audit.compare_rosters(members(), rows)
    assert result["issuer_only_overlap"] == 3
    assert result["literal_issuer_symbol_matches"] == 3
    assert result["source_securities_absent_from_checkpoint"] == 1
    assert not result["exact_fixed_source_roster"]


@pytest.mark.parametrize("side", ["source", "requested"])
@pytest.mark.parametrize(
    "mutation", ["empty", "duplicate", "invalid_cik", "invalid_symbol", "not_object"]
)
def test_invalid_or_duplicated_rosters_are_refused(side, mutation):
    source, selected = members(), requested()
    rows = source if side == "source" else selected
    if mutation == "empty":
        rows.clear()
    elif mutation == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif mutation == "invalid_cik":
        rows[0]["cik"] = "1"
    elif mutation == "invalid_symbol":
        rows[0]["symbol_at_snapshot" if side == "source" else "symbol"] = "alias/NEW"
    else:
        rows[0] = "not a security"
    with pytest.raises(ValueError):
        audit.compare_rosters(source, selected)


def test_reused_snapshot_member_key_is_refused_even_for_distinct_securities():
    rows = members()
    rows[1]["snapshot_member_key"] = rows[0]["snapshot_member_key"]
    with pytest.raises(ValueError, match="snapshot_member"):
        audit.compare_rosters(rows, requested())


def dataset(tmp_path):
    directory = tmp_path.resolve() / "synthetic-dataset"
    directory.mkdir()
    hashes = {}
    for name, filename in audit.ASSETS.items():
        raw = ("synthetic fixture " + name).encode()
        (directory / filename).write_bytes(raw)
        hashes[name] = audit.digest(raw)
    manifest = {
        "dataset_id": directory.name,
        "source_manifest_hash": "e" * 64,
        "assets": audit.ASSETS,
        "hashes": hashes,
    }
    (directory / "manifest.json").write_bytes(canonical_bytes(manifest))
    return directory, manifest


def test_verified_dataset_integrity_never_establishes_historical_membership(tmp_path):
    directory, manifest = dataset(tmp_path)
    result = audit.verify_processed_dataset(directory, "e" * 64)
    assert result["integrity_verified"] is True
    assert result["historical_membership_verified"] is False
    assert result["asset_sha256s"] == manifest["hashes"]


@pytest.mark.parametrize(
    "mutation", ["source", "id", "path", "missing_asset", "digest", "bytes", "duplicate_json"]
)
def test_processed_binding_and_actual_bytes_cannot_be_replaced(tmp_path, mutation):
    directory, manifest = dataset(tmp_path)
    if mutation == "source":
        manifest["source_manifest_hash"] = "f" * 64
    elif mutation == "id":
        manifest["dataset_id"] = "another-dataset"
    elif mutation == "path":
        manifest["assets"] = {**manifest["assets"], "features": "../features.npz"}
    elif mutation == "missing_asset":
        (directory / "features.npz").unlink()
    elif mutation == "digest":
        manifest["hashes"]["features"] = "f" * 64
    elif mutation == "bytes":
        (directory / "features.npz").write_bytes(b"altered bytes")
    raw = canonical_bytes(manifest)
    if mutation == "duplicate_json":
        raw = b'{"dataset_id":"ignored",' + raw[1:]
    (directory / "manifest.json").write_bytes(raw)
    with pytest.raises((ValueError, OSError)):
        audit.verify_processed_dataset(directory, "e" * 64)


@pytest.mark.parametrize(
    "kind", ["asset_symlink", "directory_symlink", "asset_hardlink", "oversized"]
)
def test_aliased_or_excessive_processed_inputs_fail_closed(tmp_path, monkeypatch, kind):
    directory, manifest = dataset(tmp_path)
    asset = directory / "features.npz"
    if kind == "asset_symlink":
        target = directory.parent / "outside.npz"
        asset.rename(target)
        asset.symlink_to(target)
    elif kind == "directory_symlink":
        alias = directory.parent / "directory-alias"
        alias.symlink_to(directory, target_is_directory=True)
        directory = alias
    elif kind == "asset_hardlink":
        (directory.parent / "hardlink.npz").hardlink_to(asset)
    else:
        monkeypatch.setattr(audit, "MAX_ASSET_BYTES", 1)
    with pytest.raises((ValueError, OSError)):
        audit.verify_processed_dataset(directory, "e" * 64)


def preflight(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "artifacts"
    root.mkdir(mode=0o700)
    directory = root / "identity"
    directory.mkdir(mode=0o700)
    binding = {
        "members": members(),
        "source_roster_sha256": "e" * 64,
        "cutoff": "2022-12-28T23:23:50Z",
    }
    result = {
        "baseline_filename": "previous.json",
        "reference_plan_filename": "proposal.json",
        "binding": binding,
        "results": [{"status": "pre_cutoff_SEC_identity_anchored"}] * 3
        + [{"status": "ownership_identity_unresolved"}],
        "report": {
            "statuses": {"pre_cutoff_SEC_identity_anchored": 3, "ownership_identity_unresolved": 1}
        },
    }
    raw = canonical_bytes(result)
    filename = "legacy-governance-" + audit.digest(raw) + ".json"
    path = directory / filename
    path.write_bytes(raw)
    path.chmod(0o600)
    checkpoint = root / "checkpoint"
    checkpoint.mkdir()
    requested_raw = canonical_bytes(requested()[:-1])
    (checkpoint / "requested-universe.json").write_bytes(requested_raw)
    manifest = {
        "configuration": {"assets": {"requested_universe": "requested-universe.json"}},
        "hashes": {"requested_universe": audit.digest(requested_raw)},
    }
    manifest_raw = canonical_bytes(manifest)
    (checkpoint / "manifest.json").write_bytes(manifest_raw)
    processed, pm = dataset(tmp_path)
    pm["source_manifest_hash"] = audit.digest(manifest_raw)
    (processed / "manifest.json").write_bytes(canonical_bytes(pm))
    monkeypatch.setattr(audit, "RETAINED_MANIFEST_SHA256", audit.digest(manifest_raw))
    monkeypatch.setattr(audit, "ARTIFACT_ROOT", root)
    calls = []
    monkeypatch.setattr(audit.legacy, "recompute", lambda *a: calls.append(a) or result)
    args = Namespace(
        directory=directory,
        identity_result=filename,
        governing_sources=root,
        source_capture=root,
        retained_checkpoint=checkpoint,
        processed_dataset=processed,
    )
    return args, result, calls


def test_preflight_replays_identity_before_binding_roster_and_processed_bytes(
    tmp_path, monkeypatch
):
    args, result, calls = preflight(tmp_path, monkeypatch)
    before = deepcopy(result)
    report = audit.assess(args)
    assert len(calls) == 1
    assert result == before
    assert report["identity_replay"] == "verified"
    assert report["processed_dataset"]["integrity_verified"] is True
    assert report["roster_comparison"]["source_candidates"] == 4
    assert report["roster_comparison"]["source_securities_absent_from_checkpoint"] == 1
    assert "unresolved_source_identities" in report["blockers"]
    assert "checkpoint_requested_roster_differs_from_fixed_source" in report["blockers"]
    assert report["status"] == "blocked_historical_checkpoint_reuse"
    assert report["provider_requests"] == 0
    assert report["checkpoint_dataset_binding_verified"] is False
    assert report["issue_complete"] is False


def test_altered_identity_result_is_refused_before_independent_replay(tmp_path, monkeypatch):
    args, result, calls = preflight(tmp_path, monkeypatch)
    (args.directory / args.identity_result).write_bytes(
        canonical_bytes({**result, "tampered": True})
    )
    with pytest.raises(ValueError, match="identity_result_digest_mismatch"):
        audit.assess(args)
    assert not calls


def test_independent_identity_replay_cannot_be_replaced_by_an_attested_count(tmp_path, monkeypatch):
    args, result, calls = preflight(tmp_path, monkeypatch)
    monkeypatch.setattr(audit.legacy, "recompute", lambda *a: {**result, "tampered": True})
    with pytest.raises(ValueError, match="identity_replay_mismatch"):
        audit.assess(args)


def test_unchanged_issuer_count_cannot_hide_modified_requested_security(tmp_path, monkeypatch):
    args, _, _ = preflight(tmp_path, monkeypatch)
    path = args.retained_checkpoint / "requested-universe.json"
    rows = requested()[:-1]
    rows[0]["symbol"] = "NEW"
    path.write_bytes(canonical_bytes(rows))
    with pytest.raises(ValueError, match="requested_roster_digest_mismatch"):
        audit.assess(args)


def test_cli_retains_blocked_report_and_replays_it_without_returning_success(
    tmp_path, monkeypatch, capsys
):
    args, _, _ = preflight(tmp_path, monkeypatch)
    output = args.directory.parent / "report"
    argv = ["audit_historical_checkpoint"]
    for name, value in vars(args).items():
        argv += ["--" + name.replace("_", "-"), str(value)]
    monkeypatch.setattr("sys.argv", argv + ["--output-dir", str(output)])
    assert audit.main() == 2
    captured = capsys.readouterr()
    assert '"issue_complete": false' in captured.out
    report = next(output.glob("compatibility-*.json"))
    original = report.read_bytes()
    monkeypatch.setattr("sys.argv", argv + ["--verify-report", str(report)])
    assert audit.main() == 2
    assert report.read_bytes() == original
    report.write_bytes(original + b" ")
    assert audit.main() == 1
    assert "refused" in capsys.readouterr().err
