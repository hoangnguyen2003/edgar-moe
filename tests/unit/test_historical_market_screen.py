import argparse
import json
from copy import deepcopy
from pathlib import Path

import pytest

from edgar_moe.data.historical_market_screen import replay_historical_screen
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import replay_historical_market_screen as command
from scripts.audit_historical_checkpoint import digest


def listing(ref, cusip, symbol, start="2022-08-01", end=None):
    return {
        "archive_security_id": ref,
        "cusip": cusip,
        "symbol": symbol,
        "valid_from": start,
        "valid_before": end,
        "available_by": start + "T15:00:00Z",
    }


def bar(day, symbol, volume):
    return {
        "session": day,
        "symbol": symbol,
        "available_by": day + "T21:20:00Z",
        "close": 10,
        "volume": volume,
    }


@pytest.fixture
def inputs():
    roster = [
        ("0000000001", "ALFA", "000000018"),
        ("0000000001", "ALFA.B", "000000026"),
        ("0000000002", "BETA", "000000034"),
        ("0000000003", "GAMA", "000000042"),
    ]
    members = [
        {"snapshot_member_key": symbol, "cik": cik, "symbol_at_snapshot": symbol}
        for cik, symbol, _ in roster
    ]
    results = [
        {
            "snapshot_member_key": symbol,
            "status": "pre_cutoff_SEC_identity_anchored",
            "anchor": {
                "issuer_cik": cik,
                "symbol_at_cutoff": symbol,
                "security_id": f"cusip:{cusip}",
                "available_by": "2022-12-20T15:00:00Z",
            },
        }
        for cik, symbol, cusip in roster
    ]
    identity = {
        "binding": {
            "members": members,
            "cutoff": "2022-12-28T23:23:50Z",
            "source_count": len(roster),
            "source_roster_sha256": "d" * 64,
        },
        "results": results,
    }
    listings = [
        listing("id-A", "000000018", "ALFA"),
        listing("id-B", "000000026", "ALFA.B"),
        # A renamed security and a known future delisting do not change the
        # historical population or the pre-cutoff choice.
        listing("id-C", "000000034", "OLD", end="2022-12-27"),
        listing("id-C", "000000034", "BETA", start="2022-12-27", end="2023-01-10"),
        listing("id-D", "000000042", "GAMA", start="2022-12-28"),
    ]
    bars = {
        "id-A": [bar("2022-12-27", "ALFA", 100), bar("2022-12-28", "ALFA", 100)],
        "id-B": [bar("2022-12-27", "ALFA.B", 300), bar("2022-12-28", "ALFA.B", 300)],
        "id-C": [bar("2022-12-23", "OLD", 200), bar("2022-12-28", "BETA", 200)],
        "id-D": [],
    }
    policy = {
        "lookback_start": "2022-12-22",
        "maximum_issuers": 2,
        "minimum_sessions": 2,
        "minimum_price": 5,
    }
    return identity, listings, bars, policy


def test_preserves_share_classes_and_uses_the_dated_symbol_for_each_session(inputs):
    selected, counts = replay_historical_screen(*inputs)
    assert [item["symbol"] for item in selected] == ["ALFA.B", "BETA"]
    assert [item["screen_liquidity_rank"] for item in selected] == [1, 2]
    assert selected[1]["screen_sessions"] == 2
    assert counts == {
        "insufficient_sessions": 1,
        "selected": 2,
        "additional_share_class": 1,
        "below_candidate_cutoff": 0,
    }
    assert replay_historical_screen(*inputs) == (selected, counts)


@pytest.mark.parametrize(
    "case",
    [
        "late_listing",
        "late_bar",
        "bar_before_close",
        "holiday",
        "duplicate_bar",
        "retroactive_symbol",
        "ticker_reuse_overlaps",
        "wrong_security",
        "missing_listing",
        "missing_bars",
        "unresolved_anchor",
        "duplicate_anchor",
    ],
)
def test_fails_closed_on_invalid_or_incomplete_market_evidence(inputs, case):
    identity, listings, bars, policy = inputs
    if case == "late_listing":
        listings[0]["available_by"] = "2022-12-29T12:00:00Z"
    elif case == "late_bar":
        bars["id-A"][1]["available_by"] = "2022-12-29T12:00:00Z"
    elif case == "bar_before_close":
        bars["id-A"][1]["available_by"] = "2022-12-28T14:00:00Z"
    elif case == "holiday":
        bars["id-A"][0] = bar("2022-12-26", "ALFA", 100)
    elif case == "duplicate_bar":
        bars["id-A"].append(deepcopy(bars["id-A"][1]))
    elif case == "retroactive_symbol":
        bars["id-C"][0]["symbol"] = "BETA"
    elif case == "ticker_reuse_overlaps":
        listings[2]["symbol"] = "ALFA"
    elif case == "wrong_security":
        listings[2]["cusip"] = "000000018"
    elif case == "missing_listing":
        listings.pop()
    elif case == "missing_bars":
        del bars["id-D"]
    elif case == "unresolved_anchor":
        identity["results"][0]["status"] = "ownership_identity_unresolved"
    elif case == "duplicate_anchor":
        identity["results"].append(deepcopy(identity["results"][0]))
    with pytest.raises(ValueError, match="historical_market_"):
        replay_historical_screen(identity, listings, bars, policy)


def write_fixture(path: Path, inputs):
    identity, listings, bars, policy = inputs
    path.mkdir(mode=0o700)
    values = {
        "listing-intervals.json": canonical_bytes(listings),
        "daily-bars.json": canonical_bytes(bars),
    }
    review = {
        "schema_version": 1,
        "scope": "private_historical_market_screen_replay",
        "source_version": "synthetic",
        "identity_result_sha256": "a" * 64,
        "listing_intervals_sha256": digest(values["listing-intervals.json"]),
        "daily_bars_sha256": digest(values["daily-bars.json"]),
        **policy,
    }
    values["review.json"] = canonical_bytes(review)
    for name, payload in values.items():
        target = path / name
        target.write_bytes(payload)
        target.chmod(0o600)
    return argparse.Namespace(
        inputs=path,
        identity_result="ordinary-holdings-" + "a" * 64 + ".json",
        review_sha256=digest(values["review.json"]),
        output_dir=None,
        checkpoint=None,
        processed_dataset=None,
        dataset_binding_sha256=None,
    ), identity


def test_report_is_row_free_and_private_selection_matches_commitment(tmp_path, monkeypatch, inputs):
    args, identity = write_fixture(tmp_path / "inputs", inputs)
    monkeypatch.setattr(command, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(command, "replay_identity", lambda _: (identity, "a" * 64, "b" * 64))
    args.output_dir = tmp_path / "out"
    report = command.assess(args)
    assert report["screen_counts"]["selected"] == 2
    assert report["historical_membership_verified"] is False
    assert report["publication_gate_released"] is False
    for secret in ("ALFA", "BETA", "000000026", "id-A"):
        assert secret not in json.dumps(report)
    private_file = args.output_dir / "selection.json"
    assert digest(private_file.read_bytes()) == report["selected_members_sha256"]
    assert private_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("filename", ["review.json", "listing-intervals.json", "daily-bars.json"])
def test_tampered_inputs_are_rejected_before_replaying_identity(
    tmp_path, monkeypatch, inputs, filename
):
    args, _ = write_fixture(tmp_path / "inputs", inputs)
    monkeypatch.setattr(command, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(
        command, "replay_identity", lambda _: pytest.fail("identity replay started")
    )
    path = args.inputs / filename
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="input_digest_mismatch"):
        command.assess(args)


@pytest.mark.parametrize("mode", ["verified", "different_roster", "tampered_asset"])
def test_checkpoint_and_processed_dataset_bind_to_the_replayed_selection(
    tmp_path, monkeypatch, inputs, mode
):
    args, identity = write_fixture(tmp_path / "inputs", inputs)
    monkeypatch.setattr(command, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(command, "replay_identity", lambda _: (identity, "a" * 64, "b" * 64))
    screen = command.assess(args)
    screen_sha = digest(canonical_bytes(screen) + b"\n")

    selected, _ = replay_historical_screen(*inputs)
    keys = ("cik", "symbol", "security_id")
    requested = [{key: member[key] for key in keys} for member in selected]
    if mode == "different_roster":
        requested[0]["security_id"] = "cusip:000000018"
    requested_bytes = canonical_bytes(requested)
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "requested.json").write_bytes(requested_bytes)
    checkpoint_manifest = canonical_bytes(
        {
            "dataset_id": "synthetic-checkpoint",
            "created_at": "2026-11-21T12:00:00Z",
            "source_versions": {"market": "synthetic"},
            "row_counts": {"requested_universe_members": len(requested)},
            "hashes": {"requested_universe": digest(requested_bytes)},
            "configuration": {"assets": {"requested_universe": "requested.json"}},
        }
    )
    (checkpoint / "manifest.json").write_bytes(checkpoint_manifest)

    from scripts.audit_historical_checkpoint import ASSETS

    processed = tmp_path / "synthetic-dataset"
    processed.mkdir()
    for name in ASSETS.values():
        (processed / name).write_bytes(b"fixture")
    processed_manifest = canonical_bytes(
        {
            "dataset_id": processed.name,
            "source_manifest_hash": digest(checkpoint_manifest),
            "assets": ASSETS,
            "hashes": dict.fromkeys(ASSETS, digest(b"fixture")),
        }
    )
    (processed / "manifest.json").write_bytes(processed_manifest)
    if mode == "tampered_asset":
        (processed / ASSETS["events"]).write_bytes(b"changed")

    binding = canonical_bytes(
        {
            "schema_version": 1,
            "screen_sha256": screen_sha,
            "checkpoint_manifest_sha256": digest(checkpoint_manifest),
            "processed_manifest_sha256": digest(processed_manifest),
        }
    )
    binding_path = args.inputs / "dataset-binding.json"
    binding_path.write_bytes(binding)
    binding_path.chmod(0o600)
    args.checkpoint = checkpoint
    args.processed_dataset = processed
    args.dataset_binding_sha256 = digest(binding)
    if mode == "verified":
        report = command.assess(args)
        assert report["checkpoint_dataset_binding_verified"] is True
        assert report["historical_membership_verified"] is False
        assert report["processed_dataset"]["integrity_verified"] is True
    else:
        reason = {
            "different_roster": "checkpoint_selection_mismatch",
            "tampered_asset": "processed_asset_digest_mismatch",
        }
        with pytest.raises(ValueError, match=reason[mode]):
            command.assess(args)
