"""Offline integrity check for a candidate screen and its research checkpoint.

This verifies a recorded cutoff and exact file identities. It cannot establish
that the upstream security master was itself historically available.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import orjson

from edgar_moe.data.refresh import load_universe_csv, verify_authenticated_bundle
from edgar_moe.data.storage import sha256_file


def verify_screen_trace(
    audit_path: Path,
    source_path: Path,
    screened_path: Path,
    checkpoint: Path,
    *,
    first_validation_start: date,
) -> dict[str, Any]:
    """Fail closed on missing, changed, or retrospective candidate-screen evidence."""
    audit = orjson.loads(audit_path.read_bytes())
    if not isinstance(audit, dict) or audit.get("schema_version") != 2:
        raise ValueError("A version-2 candidate-screen audit is required")
    try:
        cutoff = date.fromisoformat(audit["as_of"])
        lookback = date.fromisoformat(audit["lookback_start"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Candidate-screen cutoff dates are invalid") from exc
    if not lookback < cutoff < first_validation_start:
        raise ValueError("Candidate screen must precede the first validation period")
    if audit.get("source_sha256") != sha256_file(source_path):
        raise ValueError("Broad-universe CSV differs from its screen audit")
    if audit.get("screened_universe_sha256") != sha256_file(screened_path):
        raise ValueError("Screened-universe CSV differs from its screen audit")
    source = load_universe_csv(source_path)
    screened = load_universe_csv(screened_path)
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
    return {
        "status": "screen_trace_verified_upstream_membership_unverified",
        "screen_as_of": cutoff.isoformat(),
        "first_validation_start": first_validation_start.isoformat(),
        "candidate_count": len(screened),
        "source_manifest_sha256": sha256_file(checkpoint / "manifest.json"),
        "screen_audit_sha256": sha256_file(audit_path),
        "limitation": (
            "The cutoff is self-recorded, and the upstream SEC-to-Alpaca security "
            "master may be retrospective; this check does not establish "
            "point-in-time candidate membership."
        ),
    }
