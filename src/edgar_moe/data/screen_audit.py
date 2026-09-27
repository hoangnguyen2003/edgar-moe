"""Offline integrity check for a candidate screen and its research checkpoint.

This verifies a recorded cutoff and exact file identities. It cannot establish
that the upstream security master was itself historically available.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import orjson

from edgar_moe.data.refresh import load_universe_csv, verify_authenticated_bundle
from edgar_moe.data.storage import sha256_file


def _utc_observation(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be a UTC timestamp ending in Z")
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    if observed.utcoffset() != UTC.utcoffset(observed):
        raise ValueError(f"{label} must be UTC")
    return observed


def record_source_capture(source_path: Path, review_path: Path, output_path: Path) -> None:
    """Observe a reviewed local master now; never infer an earlier source date."""
    _verify_review_roster(source_path, review_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(
        orjson.dumps(
            {
                "schema_version": 1,
                "observed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "source_universe_sha256": sha256_file(source_path),
                "mapping_review_sha256": sha256_file(review_path),
                "source_kind": "reviewed_sec_alpaca_mapping",
                "limitation": "Self-recorded observation; no historical membership or independent timestamp.",
            },
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
        )
    )


def verify_source_capture(capture_path: Path, source_path: Path, review_path: Path) -> datetime:
    """Bind a locally observed reviewed master to both outputs, without backdating it."""
    capture = orjson.loads(capture_path.read_bytes())
    if (
        not isinstance(capture, dict)
        or set(capture)
        != {
            "schema_version",
            "observed_at_utc",
            "source_universe_sha256",
            "mapping_review_sha256",
            "source_kind",
            "limitation",
        }
        or capture["schema_version"] != 1
        or capture["source_kind"] != "reviewed_sec_alpaca_mapping"
    ):
        raise ValueError("Source-master capture record is invalid")
    if capture["source_universe_sha256"] != sha256_file(source_path):
        raise ValueError("Source-master CSV differs from its capture record")
    if capture["mapping_review_sha256"] != sha256_file(review_path):
        raise ValueError("Mapping review differs from its capture record")
    _verify_review_roster(source_path, review_path)
    return _utc_observation(capture["observed_at_utc"], "source-master observation")


def _verify_review_roster(source_path: Path, review_path: Path) -> None:
    members = load_universe_csv(source_path)
    review = orjson.loads(review_path.read_bytes())
    if not isinstance(review, list) or not all(isinstance(item, dict) for item in review):
        raise ValueError("Mapping review must be a JSON array of mappings")
    reviewed_pairs: dict[tuple[str, str], Any] = {}
    for item in review:
        pair = (str(item.get("cik", "")).zfill(10), str(item.get("symbol", "")).upper())
        if pair in reviewed_pairs:
            raise ValueError("Mapping review contains duplicate issuer-symbol pairs")
        reviewed_pairs[pair] = item.get("security_id")
    for member in members:
        pair = (member.cik, member.symbol)
        if pair not in reviewed_pairs:
            raise ValueError("Broad universe contains a mapping absent from its review")
        if member.security_id is not None and member.security_id != reviewed_pairs[pair]:
            raise ValueError("Broad universe security ID differs from its mapping review")


def verify_screen_observation_window(observed: datetime, cutoff: date, generated: datetime) -> None:
    """Reject a screen whose master or execution time cannot precede research use."""
    ny_cutoff_end = datetime.combine(
        cutoff + timedelta(days=1), time.min, tzinfo=ZoneInfo("America/New_York")
    ).astimezone(UTC)
    if observed >= ny_cutoff_end:
        raise ValueError("Source master was observed after the screen cutoff")
    if generated < ny_cutoff_end:
        raise ValueError("Screen ran before the New York cutoff date completed")
    if generated < observed:
        raise ValueError("Screen generation precedes source-master observation")


def verify_screen_trace(
    audit_path: Path,
    source_path: Path,
    screened_path: Path,
    checkpoint: Path,
    *,
    first_validation_start: date,
    source_capture_path: Path,
    mapping_review_path: Path,
    processed_dataset_dir: Path | None = None,
) -> dict[str, Any]:
    """Fail closed on missing, changed, or retrospective candidate-screen evidence."""
    audit = orjson.loads(audit_path.read_bytes())
    if not isinstance(audit, dict) or audit.get("schema_version") != 4:
        raise ValueError("A version-4 candidate-screen audit is required")
    if audit.get("purpose") != "research_screen":
        raise ValueError("Retrospective diagnostics cannot verify a research screen")
    try:
        cutoff = date.fromisoformat(audit["as_of"])
        lookback = date.fromisoformat(audit["lookback_start"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Candidate-screen cutoff dates are invalid") from exc
    if not lookback < cutoff < first_validation_start:
        raise ValueError("Candidate screen must precede the first validation period")
    observed = verify_source_capture(source_capture_path, source_path, mapping_review_path)
    if audit.get("source_capture_sha256") != sha256_file(source_capture_path):
        raise ValueError("Source-master capture differs from the screen audit")
    generated = _utc_observation(audit.get("generated_at_utc"), "screen generation")
    verify_screen_observation_window(observed, cutoff, generated)
    if generated.date() >= first_validation_start:
        raise ValueError("Screen was generated after the first validation period began")
    if audit.get("source_sha256") != sha256_file(source_path):
        raise ValueError("Broad-universe CSV differs from its screen audit")
    if audit.get("screened_universe_sha256") != sha256_file(screened_path):
        raise ValueError("Screened-universe CSV differs from its screen audit")
    source = load_universe_csv(source_path)
    screened = load_universe_csv(screened_path)
    source_by_cik = {member.cik: member for member in source}
    if any(source_by_cik.get(member.cik) != member for member in screened):
        raise ValueError("Screened universe contains a member outside the captured source master")
    if audit.get("source_members") != len(source):
        raise ValueError("Broad-universe member count differs from its screen audit")
    exclusions = audit.get("exclusions")
    if not isinstance(exclusions, dict) or exclusions.get("selected") != len(screened):
        raise ValueError("Screened member count differs from its screen audit")

    manifest = verify_authenticated_bundle(checkpoint)
    assets = manifest.configuration["assets"]
    requested = assets.get("requested_universe")
    if not isinstance(requested, str):
        raise ValueError("Checkpoint lacks a requested-universe asset")
    requested_path = (checkpoint.resolve() / requested).resolve()
    if not requested_path.is_relative_to(checkpoint.resolve()):
        raise ValueError("Requested-universe asset escapes the checkpoint")
    if orjson.loads(requested_path.read_bytes()) != [
        member.model_dump(mode="json") for member in screened
    ]:
        raise ValueError("Checkpoint requested universe differs from the screened CSV")
    if cutoff > date.fromisoformat(str(manifest.configuration["as_of"])):
        raise ValueError("Screen cutoff follows the checkpoint as-of date")
    result = {
        "status": "screen_trace_verified_upstream_membership_unverified",
        "screen_as_of": cutoff.isoformat(),
        "first_validation_start": first_validation_start.isoformat(),
        "candidate_count": len(screened),
        "source_manifest_sha256": sha256_file(checkpoint / "manifest.json"),
        "screen_audit_sha256": sha256_file(audit_path),
        "source_capture_sha256": sha256_file(source_capture_path),
        "source_master_observed_at_utc": observed.isoformat().replace("+00:00", "Z"),
        "screen_generated_at_utc": generated.isoformat().replace("+00:00", "Z"),
        "limitation": (
            "The source and screen observation times are self-recorded; the live "
            "SEC-to-Alpaca master has no historical membership intervals or independent "
            "timestamp proof. This check does not establish point-in-time membership."
        ),
    }
    if processed_dataset_dir is not None:
        processed_manifest_path = processed_dataset_dir / "manifest.json"
        processed = orjson.loads(processed_manifest_path.read_bytes())
        if (
            not isinstance(processed, dict)
            or processed.get("source_manifest_hash") != result["source_manifest_sha256"]
        ):
            raise ValueError("Processed dataset is not derived from this checkpoint")
        dataset_id = processed.get("dataset_id")
        if not isinstance(dataset_id, str) or processed_dataset_dir.name != dataset_id:
            raise ValueError("Processed dataset identity does not match its directory")
        assets = processed.get("assets")
        hashes = processed.get("hashes")
        if (
            not isinstance(assets, dict)
            or not isinstance(hashes, dict)
            or set(assets) != {"events", "daily_returns", "availability", "features"}
            or set(assets) != set(hashes)
        ):
            raise ValueError("Processed dataset asset manifest is invalid")
        for name, relative in assets.items():
            if not isinstance(relative, str) or not isinstance(hashes[name], str):
                raise ValueError("Processed dataset asset manifest is invalid")
            asset = (processed_dataset_dir.resolve() / relative).resolve()
            if not asset.is_relative_to(processed_dataset_dir.resolve()):
                raise ValueError("Processed dataset asset escapes its directory")
            if sha256_file(asset) != hashes[name]:
                raise ValueError("Processed dataset asset hash mismatch")
        result["processed_dataset_id"] = dataset_id
        result["processed_manifest_sha256"] = sha256_file(processed_manifest_path)
    return result
