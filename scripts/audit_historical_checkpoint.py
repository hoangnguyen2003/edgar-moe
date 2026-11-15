"""Offline compatibility preflight; never promote historical research evidence.

Independently replay the retained identity supplement before comparing the full
fixed source with a checkpoint's requested securities. Verify a processed
dataset's actual asset hashes separately from historical membership. A valid
old dataset can remain incompatible with the historical source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from edgar_moe.data.screen_inputs import canonical_bytes, decode_json
from scripts import enrich_historical_legacy_governance as legacy
from scripts.capture_historical_security_identity import ARTIFACT_ROOT, RETAINED_MANIFEST_SHA256
from scripts.capture_prospective_sec_index import (
    _capture_directory,
    _preflight_new_capture,
    _read_private_file,
    _write_new,
)

MAX_MANIFEST_BYTES = 4 * 1024**2
MAX_ASSET_BYTES = 512 * 1024**2
ASSETS = {
    "events": "events.parquet",
    "daily_returns": "daily-returns.parquet",
    "availability": "availability.parquet",
    "features": "features.npz",
}


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    # Walk the supplied path without resolving away a symlink before checking it.
    absolute = path.absolute()
    if ".." in absolute.parts:
        raise ValueError("aliased_evidence_directory")
    with ExitStack() as stack:
        fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        for part in absolute.parts[1:]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
        yield fd


def _read(fd: int, filename: str, maximum: int) -> tuple[bytes, str]:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}", filename) is None:
        raise ValueError("invalid_evidence_filename")
    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= maximum
        ):
            raise ValueError("invalid_or_excessive_evidence_file")
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if len(raw) != before.st_size or any(
            getattr(before, k) != getattr(after, k) for k in stable
        ):
            raise ValueError("evidence_changed_during_read")
    return raw, digest(raw)


def verify_processed_dataset(directory: Path, checkpoint_hash: str) -> dict[str, Any]:
    """Bind an existing dataset's bytes, without asserting its source was PIT."""
    with _directory(directory) as fd:
        raw, manifest_hash = _read(fd, "manifest.json", MAX_MANIFEST_BYTES)
        value = decode_json(raw)
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("dataset_id"), str)
            or directory.name != value["dataset_id"]
            or not _sha(checkpoint_hash)
            or value.get("source_manifest_hash") != checkpoint_hash
            or value.get("assets") != ASSETS
            or not isinstance(value.get("hashes"), dict)
            or set(value["hashes"]) != set(ASSETS)
        ):
            raise ValueError("processed_dataset_binding_mismatch")
        hashes = {}
        for name, filename in ASSETS.items():
            expected = value["hashes"][name]
            if not _sha(expected):
                raise ValueError("invalid_processed_asset_digest")
            _, actual = _read(fd, filename, MAX_ASSET_BYTES)
            if actual != expected:
                raise ValueError("processed_asset_digest_mismatch")
            hashes[name] = actual
        if _read(fd, "manifest.json", MAX_MANIFEST_BYTES)[1] != manifest_hash:
            raise ValueError("processed_manifest_changed_during_verification")
    return {
        "manifest_sha256": manifest_hash,
        "asset_sha256s": hashes,
        "source_manifest_sha256": checkpoint_hash,
        "integrity_verified": True,
        "historical_membership_verified": False,
    }


def compare_rosters(source: Any, requested: Any) -> dict[str, Any]:
    """Compare literal securities, retaining every source row and shared CIK."""
    if not isinstance(source, list) or not isinstance(requested, list):
        raise ValueError("invalid_roster_arrays")
    if not 1 <= len(source) <= 1000 or not 1 <= len(requested) <= 1000:
        raise ValueError("invalid_roster_count")

    def pairs(rows: list[Any], historical: bool) -> set[tuple[str, str]]:
        result = set()
        symbols = set()
        keys = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid_roster_member")
            cik = row.get("cik")
            symbol = row.get("symbol_at_snapshot" if historical else "symbol")
            if (
                not isinstance(cik, str)
                or re.fullmatch(r"[0-9]{10}", cik) is None
                or int(cik) == 0
                or not isinstance(symbol, str)
                or re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol) is None
                or symbol in symbols
                or (cik, symbol) in result
            ):
                raise ValueError("invalid_or_duplicate_roster_security")
            if historical:
                key = row.get("snapshot_member_key")
                if not _sha(key) or key in keys:
                    raise ValueError("invalid_or_duplicate_snapshot_member")
                keys.add(key)
            result.add((cik, symbol))
            symbols.add(symbol)
        return result

    source_pairs, requested_pairs = pairs(source, True), pairs(requested, False)
    source_issuers = {p[0] for p in source_pairs}
    requested_issuers = {p[0] for p in requested_pairs}
    return {
        "source_candidates": len(source_pairs),
        "source_issuers": len(source_issuers),
        "source_additional_share_class_rows": len(source_pairs) - len(source_issuers),
        "requested_securities": len(requested_pairs),
        "requested_issuers": len(requested_issuers),
        "literal_issuer_symbol_matches": len(source_pairs & requested_pairs),
        "issuer_only_overlap": len(source_issuers & requested_issuers),
        "source_securities_absent_from_checkpoint": len(source_pairs - requested_pairs),
        "checkpoint_securities_outside_fixed_source": len(requested_pairs - source_pairs),
        "source_pair_set_sha256": digest(canonical_bytes(sorted(source_pairs))),
        "requested_pair_set_sha256": digest(canonical_bytes(sorted(requested_pairs))),
        "exact_fixed_source_roster": source_pairs == requested_pairs,
        "comparison_basis": "literal_issuer_and_symbol_not_permanent_security_continuity",
        "renames_or_class_aliases_inferred": False,
    }


def assess(args: argparse.Namespace) -> dict[str, Any]:
    code_hash = digest(Path(__file__).read_bytes())
    match = re.fullmatch(r"legacy-governance-([a-f0-9]{64})\.json", args.identity_result)
    if match is None:
        raise ValueError("invalid_identity_result_filename")
    with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
        raw = _read_private_file(fd, args.identity_result, 32 * 1024**2)
    if digest(raw) != match[1]:
        raise ValueError("identity_result_digest_mismatch")
    stored = decode_json(raw)
    replayed = legacy.recompute(
        args.directory,
        stored["baseline_filename"],
        stored["reference_plan_filename"],
        args.governing_sources,
        args.source_capture,
        args.retained_checkpoint,
    )
    if canonical_bytes(stored) != canonical_bytes(replayed):
        raise ValueError("identity_replay_mismatch")
    with _directory(args.retained_checkpoint) as fd:
        manifest, manifest_hash = _read(fd, "manifest.json", MAX_MANIFEST_BYTES)
        if manifest_hash != RETAINED_MANIFEST_SHA256:
            raise ValueError("retained_manifest_digest_mismatch")
        value = decode_json(manifest)
        filename = value["configuration"]["assets"]["requested_universe"]
        requested, requested_hash = _read(fd, filename, MAX_MANIFEST_BYTES)
        if requested_hash != value["hashes"]["requested_universe"]:
            raise ValueError("requested_roster_digest_mismatch")
        comparison = compare_rosters(replayed["binding"]["members"], decode_json(requested))
        processed = verify_processed_dataset(args.processed_dataset, manifest_hash)
        if _read(fd, "manifest.json", MAX_MANIFEST_BYTES)[1] != manifest_hash:
            raise ValueError("checkpoint_manifest_changed_during_verification")
        if _read(fd, filename, MAX_MANIFEST_BYTES)[1] != requested_hash:
            raise ValueError("requested_roster_changed_during_verification")
    if digest(Path(__file__).read_bytes()) != code_hash:
        raise ValueError("preflight_algorithm_changed_during_verification")
    blockers = [
        "historical_market_continuity_unverified",
        "new_source_selection_checkpoint_dataset_study_chain_unverified",
    ]
    statuses = replayed["report"]["statuses"]
    unresolved = len(replayed["results"]) - statuses.get("pre_cutoff_SEC_identity_anchored", 0)
    if unresolved:
        blockers.append("unresolved_source_identities")
    if not comparison["exact_fixed_source_roster"]:
        blockers.append("checkpoint_requested_roster_differs_from_fixed_source")
    return {
        "schema_version": 1,
        "status": "blocked_historical_checkpoint_reuse",
        "scope": "offline_existing_checkpoint_compatibility_not_new_study_admission",
        "identity_result_sha256": match[1],
        "identity_replay": "verified",
        "identity_source_roster_sha256": replayed["binding"]["source_roster_sha256"],
        "source_cutoff": replayed["binding"]["cutoff"],
        "checkpoint_manifest_sha256": manifest_hash,
        "requested_roster_sha256": requested_hash,
        "processed_dataset": processed,
        "roster_comparison": comparison,
        "identity_statuses": statuses,
        "unresolved_identities": unresolved,
        "blockers": blockers,
        "preflight_algorithm_sha256": code_hash,
        "provider_requests": 0,
        "paid_resources": False,
        "private_rows_published": False,
        "historical_market_continuity_verified": False,
        "checkpoint_dataset_binding_verified": False,
        "publication_gate_released": False,
        "issue_complete": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "directory",
        "governing-sources",
        "source-capture",
        "retained-checkpoint",
        "processed-dataset",
    ):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--identity-result", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output-dir", type=Path)
    mode.add_argument("--verify-report", type=Path)
    args = parser.parse_args()
    try:
        if args.output_dir:
            _preflight_new_capture(args.output_dir, root=ARTIFACT_ROOT)
        report = assess(args)
        raw = canonical_bytes(report) + b"\n"
        if args.verify_report:
            with _capture_directory(args.verify_report.parent, root=ARTIFACT_ROOT) as fd:
                previous = _read_private_file(fd, args.verify_report.name, 65536)
            if (
                previous != raw
                or args.verify_report.name != "compatibility-" + digest(raw) + ".json"
            ):
                raise ValueError("preflight_report_replay_mismatch")
        else:
            with _capture_directory(args.output_dir, root=ARTIFACT_ROOT, create=True) as fd:
                _write_new(fd, "compatibility-" + digest(raw) + ".json", raw)
        print(json.dumps(report, sort_keys=True))
        # Integrity verification succeeds, but admission always remains blocked.
        return 2
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        print(
            json.dumps(
                {"status": "historical_checkpoint_preflight_refused", "provider_requests": 0}
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
