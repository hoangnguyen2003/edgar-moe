"""Append/replay an offline legacy-charter identity supplement, without HTTP.

The previous 503-row supplement must independently replay before a new source
can qualify any missing common voting title. Existing anchor payloads, source
binding and candidate order remain unchanged; market continuity stays unproven.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from edgar_moe.data.historical_governance import _par_value, qualify_ownership_voting
from edgar_moe.data.historical_identity import (
    MAX_DOCUMENT_BYTES,
    MAX_RETAINED_DOCUMENT_BYTES,
    IdentityEvidenceError,
    _ownership_subject_name,
    class_signature,
    instant,
    join_identity,
    ownership_identity,
    periodic_anchor,
)
from edgar_moe.data.historical_legacy_governance import (
    legacy_reference,
    legacy_voting_evidence,
    name_key,
)
from edgar_moe.data.screen_inputs import canonical_bytes, decode_json
from scripts import enrich_historical_identity_class as previous_supplement
from scripts.capture_historical_security_identity import (
    ARTIFACT_ROOT,
    PERIODIC_FORMS,
    RetainedCheckpoint,
    SourceCache,
    choose_filings,
    digest,
    filing_url,
    ownership_candidates,
    submissions,
)
from scripts.capture_prospective_sec_index import _capture_directory, _read_private_file, _write_new


def algorithms() -> dict[str, str]:
    return {
        **previous_supplement.algorithms(),
        "legacy_governance_sha256": digest(
            (
                Path(__file__).resolve().parents[1]
                / "src/edgar_moe/data/historical_legacy_governance.py"
            ).read_bytes()
        ),
        "legacy_supplement_sha256": digest(Path(__file__).read_bytes()),
    }


def append_results(base: dict[str, Any], additions: dict[str, Any]) -> list[dict[str, Any]]:
    members = [row["snapshot_member_key"] for row in base["results"]]
    if len(set(members)) != len(members) or set(additions) - set(members):
        raise IdentityEvidenceError("legacy_supplement_member_set_changed")
    results = []
    for original in base["results"]:
        key = original["snapshot_member_key"]
        if key not in additions:
            results.append(original)
        elif original["status"] != "ownership_identity_unresolved" or "anchor" in original:
            raise IdentityEvidenceError("legacy_supplement_existing_anchor_or_periodic_override")
        else:
            results.append(
                {
                    "snapshot_member_key": key,
                    "status": "pre_cutoff_SEC_identity_anchored",
                    "anchor": additions[key],
                }
            )
    return results


def reference_plan(raw: bytes, eligible: set[str]) -> list[dict[str, str]]:
    plan = decode_json(raw)
    if (
        not isinstance(plan, dict)
        or set(plan) != {"schema_version", "scope", "references"}
        or type(plan["schema_version"]) is not int
        or plan["schema_version"] != 1
        or plan["scope"] != "private_legacy_governing_identity_only"
        or not isinstance(plan["references"], list)
        or not 1 <= len(plan["references"]) <= 503
    ):
        raise IdentityEvidenceError("invalid_legacy_reference_plan")
    seen: set[str] = set()
    for ref in plan["references"]:
        if (
            not isinstance(ref, dict)
            or set(ref) != {"snapshot_member_key", "source_sha256"}
            or not isinstance(ref["snapshot_member_key"], str)
            or ref["snapshot_member_key"] not in eligible
            or ref["snapshot_member_key"] in seen
            or not isinstance(ref["source_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", ref["source_sha256"])
        ):
            raise IdentityEvidenceError("invalid_or_duplicate_legacy_reference_member")
        seen.add(ref["snapshot_member_key"])
    return plan["references"]


def candidate(
    member: dict[str, Any],
    proposed_sha: str,
    cache: SourceCache,
    retained: RetainedCheckpoint,
    source_fd: int,
    cutoff: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cik = member["cik"]
    rows = submissions(cik, cache, retained, cutoff)

    def registered(row: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        body = retained.filing(row)
        from_retained = body is not None
        if body is None:
            body = cache.get(filing_url(row))
        return periodic_anchor(
            body,
            row,
            cik=cik,
            symbol=member["symbol_at_snapshot"],
            cutoff=cutoff,
            maximum_document_bytes=MAX_RETAINED_DOCUMENT_BYTES
            if from_retained
            else MAX_DOCUMENT_BYTES,
        ), body

    periodic = None
    for row in choose_filings(rows, PERIODIC_FORMS, cutoff, 4):
        try:
            periodic, _ = registered(row)
            break
        except IdentityEvidenceError:
            continue
    if periodic is None or class_signature(periodic["security_title"]) != ("common", "", "voting"):
        raise IdentityEvidenceError("unsupported_legacy_current_registration")
    annuals = choose_filings(rows, {"10-K"}, cutoff, 1)
    if len(annuals) != 1:
        raise IdentityEvidenceError("missing_legacy_current_annual_index")
    annual, body = registered(annuals[0])
    if class_signature(annual["security_title"]) != class_signature(
        periodic["security_title"]
    ) or _par_value(annual["security_title"]) != _par_value(periodic["security_title"]):
        raise IdentityEvidenceError("legacy_annual_registration_class_conflict")
    reference = legacy_reference(body, cik=cik)
    key = digest(reference["url"].encode())
    meta = decode_json(_read_private_file(source_fd, key + ".json", 4096))
    source = _read_private_file(source_fd, key + ".body", MAX_DOCUMENT_BYTES)
    if (
        not isinstance(meta, dict)
        or set(meta) != {"url", "sha256", "bytes", "retrieved_at"}
        or meta["url"] != reference["url"]
        or meta["sha256"] != proposed_sha
        or digest(source) != proposed_sha
        or meta["bytes"] != len(source)
    ):
        raise IdentityEvidenceError("legacy_source_receipt_or_proposal_mismatch")
    instant(meta["retrieved_at"])
    proof = legacy_voting_evidence(
        source,
        reference=reference,
        expected_title=periodic["security_title"],
        cutoff=min(cutoff, instant(annual["available_by"])),
    )
    candidates = []
    for row in ownership_candidates(rows, cik, cache, retained, cutoff):
        source_body = cache.get(
            filing_url({**row, "primaryDocument": row["accessionNumber"] + ".txt"})
        )
        try:
            issuer_name, subject = _ownership_subject_name(source_body, row, cik)
        except IdentityEvidenceError as error:
            if str(error) == "ownership_subject_issuer_mismatch":
                continue
            raise
        own = ownership_identity(source_body, row, cik=cik, cutoff=cutoff)
        if (
            digest(name_key(issuer_name.decode("utf-8")).encode())
            != proof["issuer_name_key_sha256"]
        ):
            raise IdentityEvidenceError("legacy_ownership_subject_name_conflict")
        # Every authenticated same-subject candidate must agree; unread sources,
        # explicit classes, nonvoting titles and conflicting CUSIPs are not dropped.
        qualified = qualify_ownership_voting(periodic, own, proof)
        candidates.append((qualified, subject))
    if not candidates or len({own["cusip"] for own, _ in candidates}) != 1:
        raise IdentityEvidenceError("legacy_ownership_identifier_conflict")
    own, subject = max(
        candidates, key=lambda pair: (instant(pair[0]["accepted_at"]), pair[0]["accession"])
    )
    anchor = join_identity(periodic, own)
    anchor.update(
        ownership_subject_evidence=subject,
        legacy_governing_evidence=proof,
        legacy_governing_index_evidence={**annual, **reference},
        legacy_ownership_commitments=sorted(
            [
                {"accession": item["accession"], "document_sha256": item["document_sha256"]}
                for item, _ in candidates
            ],
            key=lambda item: item["accession"],
        ),
        identity_basis="same_SEC_subject_one_common_class_and_explicit_common_voting_in_incorporated_legacy_charter",
    )
    return anchor, meta


def recompute(
    directory: Path,
    baseline_name: str,
    plan_name: str,
    governing_sources: Path,
    source_capture: Path,
    checkpoint: Path,
) -> dict[str, Any]:
    before = algorithms()
    match = re.fullmatch(r"class-enrichment-([a-f0-9]{64})\.json", baseline_name)
    if match is None or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,180}\.json", plan_name):
        raise IdentityEvidenceError("invalid_legacy_supplement_input_names")
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        baseline_raw = _read_private_file(fd, baseline_name, 32 * 1024**2)
        if digest(baseline_raw) != match[1]:
            raise IdentityEvidenceError("legacy_baseline_digest_mismatch")
        base = decode_json(baseline_raw)
        plan_raw = _read_private_file(fd, plan_name, 128 * 1024)
    if base.get("kind") != "private_filed_class_identity_supplement":
        raise IdentityEvidenceError("unsupported_legacy_baseline_kind")
    replayed = previous_supplement.recompute(
        directory,
        base["baseline_filename"],
        base["reference_plan_filename"],
        source_capture,
        checkpoint,
    )
    if canonical_bytes(replayed) != canonical_bytes(base):
        raise IdentityEvidenceError("legacy_baseline_replay_mismatch")
    members = {m["snapshot_member_key"]: m for m in base["binding"]["members"]}
    eligible = {
        r["snapshot_member_key"]
        for r in base["results"]
        if r["status"] == "ownership_identity_unresolved"
    }
    references = reference_plan(plan_raw, eligible)
    additions: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    retained = RetainedCheckpoint(checkpoint)
    with (
        _capture_directory(directory, root=ARTIFACT_ROOT) as fd,
        _capture_directory(governing_sources, root=ARTIFACT_ROOT) as source_fd,
    ):
        cache = SourceCache(fd, None, 1)
        for ref in references:
            key = ref["snapshot_member_key"]
            additions[key], receipt = candidate(
                members[key],
                ref["source_sha256"],
                cache,
                retained,
                source_fd,
                instant(base["binding"]["cutoff"]),
            )
            sources[digest(receipt["url"].encode())] = receipt
        results = append_results(base, additions)
        value = {
            "schema_version": 1,
            "kind": "private_legacy_governing_identity_supplement",
            "baseline_filename": baseline_name,
            "baseline_sha256": digest(baseline_raw),
            "binding": base["binding"],
            "reference_plan_filename": plan_name,
            "reference_plan_sha256": digest(plan_raw),
            "algorithms": before,
            "source_receipts": cache.used,
            "governing_source_receipts": sources,
            "results": results,
            "report": {
                "source_count": base["report"]["source_count"],
                "processed": len(results),
                "baseline_anchors": sum("anchor" in r for r in base["results"]),
                "anchors_added": len(additions),
                "baseline_anchor_payloads_changed": 0,
                "statuses": dict(Counter(r["status"] for r in results)),
                "provider_requests": 0,
                "paid_resources": False,
                "historical_market_continuity_verified": False,
                "checkpoint_dataset_binding_verified": False,
                "current_asset_directory_used": False,
                "issue_complete": False,
            },
        }
    if before != algorithms():
        raise IdentityEvidenceError("legacy_algorithm_changed_during_replay")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--baseline")
    parser.add_argument("--references")
    parser.add_argument("--verify")
    parser.add_argument("--governing-sources", required=True, type=Path)
    parser.add_argument("--source-capture", required=True, type=Path)
    parser.add_argument("--retained-checkpoint", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            match = re.fullmatch(r"legacy-governance-([a-f0-9]{64})\.json", args.verify)
            if match is None or args.baseline or args.references:
                raise IdentityEvidenceError("invalid_legacy_replay_arguments")
            with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
                raw = _read_private_file(fd, args.verify, 32 * 1024**2)
            if digest(raw) != match[1]:
                raise IdentityEvidenceError("legacy_supplement_digest_mismatch")
            stored = decode_json(raw)
            value = recompute(
                args.directory,
                stored["baseline_filename"],
                stored["reference_plan_filename"],
                args.governing_sources,
                args.source_capture,
                args.retained_checkpoint,
            )
            if canonical_bytes(value) != canonical_bytes(stored):
                raise IdentityEvidenceError("legacy_supplement_replay_mismatch")
            status = "offline_legacy_supplement_replay_verified"
        else:
            if not args.baseline or not args.references:
                raise IdentityEvidenceError("missing_legacy_supplement_inputs")
            value = recompute(
                args.directory,
                args.baseline,
                args.references,
                args.governing_sources,
                args.source_capture,
                args.retained_checkpoint,
            )
            raw = canonical_bytes(value) + b"\n"
            with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
                _write_new(fd, "legacy-governance-" + digest(raw) + ".json", raw)
            status = "private_legacy_supplement_captured"
        print(
            json.dumps(
                {"status": status, "result_sha256": digest(raw), **value["report"]}, sort_keys=True
            )
        )
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, UnicodeDecodeError) as error:
        print(json.dumps({"refused": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
