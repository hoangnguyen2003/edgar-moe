"""Append/replay a source-qualified class redesignation; never admit a study."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from edgar_moe.data.historical_class_charter import _name as legal_name_key
from edgar_moe.data.historical_class_redesignation import (
    qualify_redesignated_class,
    redesignation_evidence,
    redesignation_reference,
)
from edgar_moe.data.historical_governance import _par_value
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
from edgar_moe.data.screen_inputs import canonical_bytes, decode_json
from scripts import enrich_historical_proxy_definition as previous
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
from scripts.enrich_historical_legacy_governance import append_results


def algorithms() -> dict[str, str]:
    return {
        **previous.algorithms(),
        "class_redesignation_algorithm_sha256": digest(
            (
                Path(__file__).resolve().parents[1]
                / "src/edgar_moe/data/historical_class_redesignation.py"
            ).read_bytes()
        ),
        "class_redesignation_supplement_sha256": digest(Path(__file__).read_bytes()),
    }


def references(raw: bytes, eligible: set[str]) -> list[dict[str, str]]:
    value = decode_json(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "scope", "references"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["scope"] != "private_one_for_one_class_redesignation_only"
        or not isinstance(value["references"], list)
        or not 1 <= len(value["references"]) <= 503
    ):
        raise IdentityEvidenceError("invalid_redesignation_reference_plan")
    seen = set()
    for ref in value["references"]:
        if (
            not isinstance(ref, dict)
            or set(ref) != {"snapshot_member_key", "submission_sha256", "primary_filename"}
            or not all(isinstance(v, str) for v in ref.values())
            or ref["snapshot_member_key"] not in eligible
            or ref["snapshot_member_key"] in seen
            or re.fullmatch(r"[a-f0-9]{64}", ref["submission_sha256"]) is None
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.html?", ref["primary_filename"])
            is None
        ):
            raise IdentityEvidenceError("invalid_or_duplicate_redesignation_reference")
        seen.add(ref["snapshot_member_key"])
    return value["references"]


def candidate(
    member: dict[str, Any],
    ref: dict[str, str],
    cache: SourceCache,
    source: SourceCache,
    retained: RetainedCheckpoint,
    cutoff: datetime,
) -> dict[str, Any]:
    cik = member["cik"]
    rows = submissions(cik, cache, retained, cutoff)

    def document(row: dict[str, Any]) -> tuple[bytes, int]:
        raw = retained.filing(row)
        from_retained = raw is not None
        if raw is None:
            raw = cache.get(filing_url(row))
        return raw, MAX_RETAINED_DOCUMENT_BYTES if from_retained else MAX_DOCUMENT_BYTES

    def registered(row: dict[str, Any], raw: bytes, limit: int) -> dict[str, Any]:
        return periodic_anchor(
            raw,
            row,
            cik=cik,
            symbol=member["symbol_at_snapshot"],
            cutoff=cutoff,
            maximum_document_bytes=limit,
        )

    periodic = None
    for row in choose_filings(rows, PERIODIC_FORMS, cutoff, 4):
        raw, limit = document(row)
        try:
            periodic = registered(row, raw, limit)
            break
        except IdentityEvidenceError:
            continue
    if periodic is None or class_signature(periodic["security_title"]) != ("common", "", ""):
        raise IdentityEvidenceError("unsupported_redesignation_periodic_registration")
    annuals = choose_filings(rows, {"10-K"}, cutoff, 1)
    if len(annuals) != 1:
        raise IdentityEvidenceError("missing_redesignation_current_annual")
    raw, limit = document(annuals[0])
    annual = registered(annuals[0], raw, limit)
    if class_signature(annual["security_title"]) != class_signature(
        periodic["security_title"]
    ) or _par_value(annual["security_title"]) != _par_value(periodic["security_title"]):
        raise IdentityEvidenceError("redesignation_annual_periodic_registration_conflict")
    reference = redesignation_reference(raw, parent_url=filing_url(annuals[0]), cik=cik)
    full = source.get(
        filing_url(
            {
                "cik": cik,
                "accessionNumber": reference["accession"],
                "primaryDocument": reference["accession"] + ".txt",
            }
        )
    )
    if digest(full) != ref["submission_sha256"]:
        raise IdentityEvidenceError("redesignation_source_proposal_digest_mismatch")
    proof = redesignation_evidence(
        full,
        reference=reference,
        primary_filename=ref["primary_filename"],
        cik=cik,
        expected_title=periodic["security_title"],
        cutoff=min(cutoff, instant(annual["available_by"])),
    )
    qualified = []
    refusals: Counter[str] = Counter()
    for row in ownership_candidates(rows, cik, cache, retained, cutoff):
        body = cache.get(filing_url(row | {"primaryDocument": row["accessionNumber"] + ".txt"}))
        try:
            name, subject = _ownership_subject_name(body, row, cik)
        except IdentityEvidenceError as error:
            if str(error) == "ownership_subject_issuer_mismatch":
                refusals[str(error)] += 1
                continue
            raise
        if digest(legal_name_key(name.decode("utf-8")).encode()) != proof["issuer_name_sha256"]:
            raise IdentityEvidenceError("redesignation_ownership_subject_name_conflict")
        own = ownership_identity(body, row, cik=cik, cutoff=cutoff)
        item = qualify_redesignated_class(periodic, own, proof)
        item["ownership_subject_evidence"] = subject
        qualified.append(item)
    if not qualified or len({item["cusip"] for item in qualified}) != 1:
        raise IdentityEvidenceError("redesignation_ownership_identifier_conflict")
    own = max(qualified, key=lambda item: (instant(item["accepted_at"]), item["accession"]))
    anchor = join_identity(periodic, own)
    anchor.update(
        identity_basis="same_SEC_subject_and_incorporated_one_for_one_class_redesignation",
        current_annual_redesignation_reference={
            "annual_registration": annual,
            "incorporated_charter": reference,
        },
        post_effective_ownership_commitments=sorted(
            [
                {"accession": item["accession"], "document_sha256": item["document_sha256"]}
                for item in qualified
            ],
            key=lambda item: item["accession"],
        ),
        discovery_refusals=dict(refusals),
    )
    return anchor


def recompute(
    directory: Path,
    baseline_name: str,
    plan_name: str,
    governing_sources: Path,
    proxy_sources: Path,
    redesignation_sources: Path,
    source_capture: Path,
    checkpoint: Path,
) -> dict[str, Any]:
    before = algorithms()
    match = re.fullmatch(r"proxy-definition-([a-f0-9]{64})\.json", baseline_name)
    if match is None or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,180}\.json", plan_name) is None:
        raise IdentityEvidenceError("invalid_redesignation_input_names")
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        baseline_raw = _read_private_file(fd, baseline_name, 32 * 1024**2)
        plan_raw = _read_private_file(fd, plan_name, 1024**2)
    if digest(baseline_raw) != match[1]:
        raise IdentityEvidenceError("redesignation_baseline_digest_mismatch")
    base = decode_json(baseline_raw)
    if base.get("kind") != "private_issuer_proxy_definition_supplement":
        raise IdentityEvidenceError("unsupported_redesignation_baseline_kind")
    replayed = previous.recompute(
        directory,
        base["baseline_filename"],
        base["reference_plan_filename"],
        governing_sources,
        proxy_sources,
        source_capture,
        checkpoint,
    )
    if canonical_bytes(replayed) != canonical_bytes(base):
        raise IdentityEvidenceError("redesignation_baseline_replay_mismatch")
    members = {m["snapshot_member_key"]: m for m in base["binding"]["members"]}
    eligible = {
        r["snapshot_member_key"]
        for r in base["results"]
        if r["status"] == "ownership_identity_unresolved"
    }
    plan = references(plan_raw, eligible)
    retained = RetainedCheckpoint(checkpoint)
    with (
        _capture_directory(directory, root=ARTIFACT_ROOT) as fd,
        _capture_directory(redesignation_sources, root=ARTIFACT_ROOT) as source_fd,
    ):
        cache, source = SourceCache(fd, None, 1), SourceCache(source_fd, None, 1)
        additions = {
            ref["snapshot_member_key"]: candidate(
                members[ref["snapshot_member_key"]],
                ref,
                cache,
                source,
                retained,
                instant(base["binding"]["cutoff"]),
            )
            for ref in plan
        }
        results = append_results(base, additions)
        value = {
            "schema_version": 1,
            "kind": "private_charter_class_redesignation_supplement",
            "baseline_filename": baseline_name,
            "baseline_sha256": digest(baseline_raw),
            "binding": base["binding"],
            "reference_plan_filename": plan_name,
            "reference_plan_sha256": digest(plan_raw),
            "algorithms": before,
            "source_receipts": cache.used,
            "redesignation_source_receipts": source.used,
            "results": results,
            "report": {
                "source_count": base["report"]["source_count"],
                "processed": len(results),
                "baseline_anchors": sum("anchor" in r for r in base["results"]),
                "anchors_added": len(additions),
                "baseline_anchor_payloads_changed": 0,
                "statuses": dict(Counter(r["status"] for r in results)),
                "documented_one_for_one_redesignation_used": True,
                "generic_class_aliases_added": False,
                "equity_family_aliases_added": False,
                "voting_classes_merged": False,
                "provider_requests": 0,
                "paid_resources": False,
                "historical_market_continuity_verified": False,
                "checkpoint_dataset_binding_verified": False,
                "publication_gate_released": False,
                "issue_complete": False,
            },
        }
    if algorithms() != before:
        raise IdentityEvidenceError("redesignation_algorithm_changed_during_replay")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in [
        "directory",
        "governing-sources",
        "proxy-sources",
        "redesignation-sources",
        "source-capture",
        "retained-checkpoint",
    ]:
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ["baseline", "references", "verify"]:
        parser.add_argument("--" + name)
    args = parser.parse_args()
    try:
        stored = None
        if args.verify:
            match = re.fullmatch(r"class-redesignation-([a-f0-9]{64})\.json", args.verify)
            if match is None or args.baseline or args.references:
                raise IdentityEvidenceError("invalid_redesignation_replay_arguments")
            with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
                raw = _read_private_file(fd, args.verify, 32 * 1024**2)
            if digest(raw) != match[1]:
                raise IdentityEvidenceError("redesignation_result_digest_mismatch")
            stored = decode_json(raw)
            baseline, plan = stored["baseline_filename"], stored["reference_plan_filename"]
        else:
            if not args.baseline or not args.references:
                raise IdentityEvidenceError("missing_redesignation_inputs")
            baseline, plan = args.baseline, args.references
        value = recompute(
            args.directory,
            baseline,
            plan,
            args.governing_sources,
            args.proxy_sources,
            args.redesignation_sources,
            args.source_capture,
            args.retained_checkpoint,
        )
        if stored is not None:
            if canonical_bytes(stored) != canonical_bytes(value):
                raise IdentityEvidenceError("redesignation_result_replay_mismatch")
            status = "offline_class_redesignation_replay_verified"
        else:
            raw = canonical_bytes(value) + b"\n"
            with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
                _write_new(fd, "class-redesignation-" + digest(raw) + ".json", raw)
            status = "private_class_redesignation_supplement_captured"
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
