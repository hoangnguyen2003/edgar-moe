"""Append or replay private identities supported by same-filing capital-stock tags."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from edgar_moe.data import historical_capital_stock_tags
from edgar_moe.data.historical_governance import _par_value
from edgar_moe.data.historical_identity import (
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
from scripts import enrich_historical_capital_definition as previous
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

KIND = "private_issuer_capital_stock_tags_supplement"
PREFIX = "capital-stock-tags"
PLAN_SCOPE = "private_same_filing_capital_stock_tags_only"
SOURCE_ARGS = (
    "governing-sources",
    "proxy-sources",
    "redesignation-sources",
    "bank-sources",
    "bank-ownership-sources",
    "capital-sources",
    "source-capture",
    "retained-checkpoint",
)


def algorithms() -> dict[str, str]:
    return {
        **previous.algorithms(),
        "capital_stock_tags_algorithm_sha256": digest(
            (
                Path(__file__).resolve().parents[1]
                / "src/edgar_moe/data/historical_capital_stock_tags.py"
            ).read_bytes()
        ),
        "capital_stock_tags_supplement_sha256": digest(Path(__file__).read_bytes()),
    }


def member_keys(raw: bytes, eligible: set[str]) -> list[str]:
    plan = decode_json(raw)
    if (
        not isinstance(plan, dict)
        or set(plan) != {"schema_version", "scope", "snapshot_member_keys"}
        or type(plan["schema_version"]) is not int
        or plan["schema_version"] != 1
        or plan["scope"] != PLAN_SCOPE
    ):
        raise IdentityEvidenceError("invalid_capital_stock_tags_plan")
    keys = plan["snapshot_member_keys"]
    if (
        not isinstance(keys, list)
        or not 1 <= len(keys) <= len(eligible)
        or any(not isinstance(key, str) or key not in eligible for key in keys)
        or len(set(keys)) != len(keys)
    ):
        raise IdentityEvidenceError("invalid_or_duplicate_capital_stock_tags_member")
    return keys


def candidate(
    member: dict[str, Any], cache: SourceCache, retained: RetainedCheckpoint, cutoff: datetime
) -> dict[str, Any]:
    cik = member["cik"]
    rows = submissions(cik, cache, retained, cutoff)
    for row in choose_filings(rows, PERIODIC_FORMS, cutoff, 4):
        raw = retained.filing(row)
        if raw is None:
            raw = cache.get(filing_url(row))
        try:
            periodic = periodic_anchor(
                raw,
                row,
                cik=cik,
                symbol=member["symbol_at_snapshot"],
                cutoff=cutoff,
                maximum_document_bytes=MAX_RETAINED_DOCUMENT_BYTES,
            )
        except IdentityEvidenceError:
            continue
        break
    else:
        raise IdentityEvidenceError("missing_capital_stock_periodic_registration")

    periodic["common_stock_tag_evidence"] = (
        historical_capital_stock_tags.qualify_tagged_capital_stock(raw, periodic, cutoff=cutoff)
    )
    registered_par = _par_value(periodic["security_title"])
    reports = []
    for row in ownership_candidates(rows, cik, cache, retained, cutoff):
        body = cache.get(filing_url({**row, "primaryDocument": row["accessionNumber"] + ".txt"}))
        # A cached but unparseable candidate leaves agreement unverified.
        _ownership_subject_name(body, row, cik)
        report = ownership_identity(body, row, cik=cik, cutoff=cutoff)
        if class_signature(report["security_title"]) != ("common", "", "") or (
            "$" in report["security_title"]
            and _par_value(report["security_title"]) != registered_par
        ):
            raise IdentityEvidenceError("capital_stock_ownership_class_conflict")
        reports.append(report)
    if len(reports) < 2 or len({report["cusip"] for report in reports}) != 1:
        raise IdentityEvidenceError("insufficient_or_conflicting_capital_stock_ownership")

    own = max(reports, key=lambda report: (instant(report["available_by"]), report["accession"]))
    result = join_identity(
        periodic,
        {
            **own,
            "reported_security_title": own["security_title"],
            "security_title": periodic["security_title"],
        },
    )
    result.update(
        identity_basis="same_SEC_subject_common_stock_tags_and_agreeing_filed_ownership",
        corroborating_ownership_commitments=sorted(
            (
                {"accession": report["accession"], "document_sha256": report["document_sha256"]}
                for report in reports
            ),
            key=lambda report: report["accession"],
        ),
    )
    return result


def recompute(
    directory: Path,
    baseline_name: str,
    plan_name: str,
    governing_sources: Path,
    proxy_sources: Path,
    redesignation_sources: Path,
    bank_sources: Path,
    bank_ownership_sources: Path,
    capital_sources: Path,
    source_capture: Path,
    checkpoint: Path,
) -> dict[str, Any]:
    before = algorithms()
    match = re.fullmatch(r"capital-definition-([a-f0-9]{64})\.json", baseline_name)
    if match is None or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,180}\.json", plan_name) is None:
        raise IdentityEvidenceError("invalid_capital_stock_tags_input_names")
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        baseline_raw = _read_private_file(fd, baseline_name, 32 * 1024**2)
        plan_raw = _read_private_file(fd, plan_name, 1024**2)
    if digest(baseline_raw) != match[1]:
        raise IdentityEvidenceError("capital_stock_tags_baseline_digest_mismatch")
    base = decode_json(baseline_raw)
    if not isinstance(base, dict) or base.get("kind") != previous.KIND:
        raise IdentityEvidenceError("capital_stock_tags_baseline_kind_mismatch")
    replayed = previous.recompute(
        directory,
        base["baseline_filename"],
        base["reference_plan_filename"],
        governing_sources,
        proxy_sources,
        redesignation_sources,
        bank_sources,
        bank_ownership_sources,
        capital_sources,
        source_capture,
        checkpoint,
    )
    if canonical_bytes(replayed) != canonical_bytes(base):
        raise IdentityEvidenceError("capital_stock_tags_parent_replay_mismatch")
    members = {member["snapshot_member_key"]: member for member in base["binding"]["members"]}
    eligible = {
        row["snapshot_member_key"]
        for row in base["results"]
        if row["status"] == "ownership_identity_unresolved"
    }
    keys = member_keys(plan_raw, eligible)
    cutoff = instant(base["binding"]["cutoff"])
    retained = RetainedCheckpoint(checkpoint)
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        cache = SourceCache(fd, None, 1)
        additions = {key: candidate(members[key], cache, retained, cutoff) for key in keys}
        results = append_results(base, additions)
    if algorithms() != before:
        raise IdentityEvidenceError("capital_stock_tags_algorithm_changed_during_replay")
    return {
        "schema_version": 1,
        "kind": KIND,
        "baseline_filename": baseline_name,
        "baseline_sha256": digest(baseline_raw),
        "reference_plan_filename": plan_name,
        "reference_plan_sha256": digest(plan_raw),
        "binding": base["binding"],
        "algorithms": before,
        "source_receipts": cache.used,
        "results": results,
        "report": {
            "source_count": base["report"]["source_count"],
            "processed": len(results),
            "baseline_anchors": sum("anchor" in row for row in base["results"]),
            "anchors_added": len(additions),
            "baseline_anchor_payloads_changed": 0,
            "statuses": dict(Counter(row["status"] for row in results)),
            "generic_class_or_equity_family_aliases_added": False,
            "provider_requests": 0,
            "paid_resources": False,
            "historical_market_continuity_verified": False,
            "checkpoint_dataset_binding_verified": False,
            "publication_gate_released": False,
            "issue_complete": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("directory", *SOURCE_ARGS):
        parser.add_argument("--" + name, required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--baseline")
    mode.add_argument("--verify")
    parser.add_argument("--references")
    args = parser.parse_args()
    if bool(args.baseline) != bool(args.references):
        parser.error("--baseline and --references must be specified together")
    try:
        stored = None
        if args.verify:
            match = re.fullmatch(r"capital-stock-tags-([a-f0-9]{64})\.json", args.verify)
            if match is None:
                raise IdentityEvidenceError("invalid_capital_stock_tags_result_name")
            with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
                raw = _read_private_file(fd, args.verify, 32 * 1024**2)
            if digest(raw) != match[1]:
                raise IdentityEvidenceError("capital_stock_tags_result_digest_mismatch")
            stored = decode_json(raw)
            if not isinstance(stored, dict) or stored.get("kind") != KIND:
                raise IdentityEvidenceError("capital_stock_tags_result_kind_mismatch")
            baseline, plan = stored["baseline_filename"], stored["reference_plan_filename"]
        else:
            baseline, plan = args.baseline, args.references
        value = recompute(
            args.directory,
            baseline,
            plan,
            *(getattr(args, name.replace("-", "_")) for name in SOURCE_ARGS),
        )
        raw = canonical_bytes(value) + b"\n"
        if stored is not None:
            if canonical_bytes(value) != canonical_bytes(stored):
                raise IdentityEvidenceError("capital_stock_tags_result_replay_mismatch")
            status = "offline_capital_stock_tags_replay_verified"
        else:
            with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
                _write_new(fd, PREFIX + "-" + digest(raw) + ".json", raw)
            status = "private_capital_stock_tags_supplement_captured"
        print(json.dumps({"status": status, "result_sha256": digest(raw), **value["report"]}))
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        print(json.dumps({"refused": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
