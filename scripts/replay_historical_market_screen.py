"""Replay reviewed fixed-cohort screen inputs offline and emit only aggregate commitments."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from edgar_moe.data import historical_market_screen
from edgar_moe.data.refresh import verify_authenticated_bundle
from edgar_moe.data.screen_inputs import canonical_bytes, decode_json, screen_algorithm_digest
from scripts.audit_historical_checkpoint import (
    MAX_MANIFEST_BYTES,
    _directory,
    _read,
    digest,
    replay_identity,
    verify_processed_dataset,
)
from scripts.capture_historical_security_identity import ARTIFACT_ROOT
from scripts.capture_prospective_sec_index import (
    _capture_directory,
    _preflight_new_capture,
    _read_private_file,
    _write_new,
)
from scripts.enrich_historical_ordinary_holdings import SOURCE_ARGS

_REVIEW_FIELDS = {
    "schema_version",
    "scope",
    "identity_result_sha256",
    "source_version",
    "listing_intervals_sha256",
    "daily_bars_sha256",
    "lookback_start",
    "maximum_issuers",
    "minimum_sessions",
    "minimum_price",
}


def _pinned(fd: int, filename: str, limit: int, expected: Any) -> Any:
    if not isinstance(expected, str) or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise ValueError("historical_market_invalid_digest")
    payload = _read_private_file(fd, filename, limit)
    if digest(payload) != expected:
        raise ValueError("historical_market_input_digest_mismatch")
    return decode_json(payload)


def verify_inputs(
    fd: int, review_digest: str, identity_digest: str
) -> tuple[dict[str, Any], Any, Any]:
    review = _pinned(fd, "review.json", 65536, review_digest)
    if (
        not isinstance(review, dict)
        or set(review) != _REVIEW_FIELDS
        or type(review["schema_version"]) is not int
        or review["schema_version"] != 1
        or review["scope"] != "private_historical_market_screen_replay"
        or review["identity_result_sha256"] != identity_digest
        or not isinstance(review["source_version"], str)
        or not 1 <= len(review["source_version"].strip()) <= 240
    ):
        raise ValueError("historical_market_invalid_review")
    return (
        review,
        _pinned(fd, "listing-intervals.json", 8 * 1024**2, review["listing_intervals_sha256"]),
        _pinned(fd, "daily-bars.json", 64 * 1024**2, review["daily_bars_sha256"]),
    )


def _verify_dataset_binding(
    checkpoint: Path,
    processed: Path,
    binding: Any,
    screen: dict[str, Any],
    selected: list[dict[str, Any]],
) -> dict[str, Any]:
    if (
        not isinstance(binding, dict)
        or set(binding)
        != {
            "schema_version",
            "screen_sha256",
            "checkpoint_manifest_sha256",
            "processed_manifest_sha256",
        }
        or type(binding["schema_version"]) is not int
        or binding["schema_version"] != 1
        or binding["screen_sha256"] != digest(canonical_bytes(screen) + b"\n")
    ):
        raise ValueError("historical_market_invalid_dataset_binding")
    with _directory(checkpoint) as fd:
        _, checkpoint_sha = _read(fd, "manifest.json", MAX_MANIFEST_BYTES)
        if checkpoint_sha != binding["checkpoint_manifest_sha256"]:
            raise ValueError("historical_market_checkpoint_digest_mismatch")
        manifest = verify_authenticated_bundle(checkpoint)
        name = manifest.configuration["assets"]["requested_universe"]
        requested_bytes, requested_sha = _read(fd, name, MAX_MANIFEST_BYTES)
        if requested_sha != manifest.hashes.get("requested_universe"):
            raise ValueError("historical_market_requested_roster_digest_mismatch")
        requested = decode_json(requested_bytes)
        fields = ("cik", "symbol", "security_id")
        if not isinstance(requested, list) or [
            tuple(item[key] for key in fields) for item in requested
        ] != [tuple(item[key] for key in fields) for item in selected]:
            raise ValueError("historical_market_checkpoint_selection_mismatch")
        dataset = verify_processed_dataset(processed, checkpoint_sha)
        if dataset["manifest_sha256"] != binding["processed_manifest_sha256"]:
            raise ValueError("historical_market_processed_digest_mismatch")
        if _read(fd, "manifest.json", MAX_MANIFEST_BYTES)[1] != checkpoint_sha:
            raise ValueError("historical_market_checkpoint_changed")
    return {
        "checkpoint_dataset_binding_verified": True,
        "checkpoint_manifest_sha256": checkpoint_sha,
        "checkpoint_requested_universe_sha256": requested_sha,
        "processed_dataset": dataset,
    }


def assess(args: argparse.Namespace) -> dict[str, Any]:
    algorithms = {
        "liquidity_algorithm_sha256": screen_algorithm_digest(),
        "historical_screen_algorithm_sha256": digest(
            Path(historical_market_screen.__file__).read_bytes()
        ),
        "historical_screen_replay_sha256": digest(Path(__file__).read_bytes()),
    }
    # Check the independent pin and input bytes before replaying the expensive
    # source chain. replay_identity rechecks the result's filename digest.
    binding_args = (args.checkpoint, args.processed_dataset, args.dataset_binding_sha256)
    if any(value is not None for value in binding_args) and any(
        value is None for value in binding_args
    ):
        raise ValueError("historical_market_missing_dataset_binding_inputs")
    identity_digest = args.identity_result.removesuffix(".json").rsplit("-", 1)[-1]
    with _capture_directory(args.inputs, root=ARTIFACT_ROOT) as fd:
        policy, listings, bars = verify_inputs(fd, args.review_sha256, identity_digest)
        binding = (
            _pinned(fd, "dataset-binding.json", 65536, args.dataset_binding_sha256)
            if args.dataset_binding_sha256 is not None
            else None
        )
    identity, verified_digest, identity_algorithms = replay_identity(args)
    if verified_digest != identity_digest:
        raise ValueError("historical_market_identity_digest_mismatch")
    selected, counts = historical_market_screen.replay_historical_screen(
        identity, listings, bars, policy
    )
    selection_bytes = canonical_bytes(selected) + b"\n"
    result = {
        "schema_version": 1,
        "status": "historical_screen_replay_verified",
        "scope": "offline_replay_of_reviewed_inputs",
        "identity_result_sha256": verified_digest,
        "identity_replay_algorithms_sha256": identity_algorithms,
        "source_roster_sha256": identity["binding"]["source_roster_sha256"],
        "cutoff": identity["binding"]["cutoff"],
        "screen_review_sha256": args.review_sha256,
        "listing_intervals_sha256": policy["listing_intervals_sha256"],
        "daily_bars_sha256": policy["daily_bars_sha256"],
        "selected_members_sha256": digest(selection_bytes),
        "screen_counts": counts,
        "candidate_count": identity["binding"]["source_count"],
        "algorithms": algorithms,
        "checkpoint_dataset_binding_verified": False,
        "historical_membership_verified": False,
        "publication_gate_released": False,
    }
    report = result
    if args.checkpoint is not None:
        report = result | {
            "dataset_review_sha256": args.dataset_binding_sha256,
            **_verify_dataset_binding(
                args.checkpoint, args.processed_dataset, binding, result, selected
            ),
        }
    if args.output_dir is not None:
        with _capture_directory(args.output_dir, root=ARTIFACT_ROOT, create=True) as fd:
            _write_new(fd, "selection.json", selection_bytes)
            _write_new(fd, "screen.json", canonical_bytes(result) + b"\n")
            if args.checkpoint is not None:
                _write_new(fd, "dataset-report.json", canonical_bytes(report) + b"\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("directory", *SOURCE_ARGS):
        parser.add_argument(
            "--" + name,
            type=Path,
            required=name
            in {"directory", "governing-sources", "source-capture", "retained-checkpoint"},
        )
    parser.add_argument("--identity-result", required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--review-sha256", required=True, help="Independently retained review hash")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--processed-dataset", type=Path)
    parser.add_argument(
        "--dataset-binding-sha256", help="Independent checkpoint and dataset review hash"
    )
    parser.add_argument("--output-dir", type=Path, help="New private directory for selected rows")
    args = parser.parse_args()
    try:
        if args.output_dir is not None:
            _preflight_new_capture(args.output_dir, root=ARTIFACT_ROOT)
        result = assess(args)
    except (OSError, TypeError, KeyError, ValueError, RuntimeError):
        print(json.dumps({"status": "historical_screen_replay_refused"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
