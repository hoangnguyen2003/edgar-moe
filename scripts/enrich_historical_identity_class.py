"""Append/replay a private holdings-class supplement to a verified identity result.

The original result and 503-row binding are never rewritten. This command has
no live mode or HTTP client. Its reference proposal and exact source receipts
remain private, and every existing anchor must survive unchanged.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from edgar_moe.data.historical_holdings_class import (
    filed_nonvoting_class,
    normalized_name,
    qualify_nonvoting_ownership,
)
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
from scripts import verify_historical_security_identity as baseline_verifier
from scripts.capture_historical_security_identity import (
    ARTIFACT_ROOT,
    PERIODIC_FORMS,
    RetainedCheckpoint,
    SourceCache,
    choose_filings,
    code_identity,
    digest,
    filing_url,
    ownership_candidates,
    submissions,
)
from scripts.capture_prospective_sec_index import _capture_directory, _read_private_file, _write_new


def algorithms() -> dict[str, str]:
    return {
        **code_identity(),
        "baseline_verifier_sha256": digest(Path(baseline_verifier.__file__).read_bytes()),
        "holdings_class_algorithm_sha256": digest(
            (
                Path(__file__).resolve().parents[1]
                / "src/edgar_moe/data/historical_holdings_class.py"
            ).read_bytes()
        ),
        "supplement_algorithm_sha256": digest(Path(__file__).read_bytes()),
    }


def reference_plan(raw: bytes, cache: SourceCache, cutoff: datetime) -> list[dict[str, Any]]:
    plan = decode_json(raw)
    if (
        not isinstance(plan, dict)
        or set(plan)
        != {"schema_version", "scope", "manager_cik", "manager_index_sha256", "references"}
        or type(plan["schema_version"]) is not int
        or plan["schema_version"] != 1
        or plan["scope"] != "private_identity_corroboration_only"
        or not isinstance(plan["manager_cik"], str)
        or re.fullmatch(r"[0-9]{10}", plan["manager_cik"]) is None
        or int(plan["manager_cik"]) == 0
        or not isinstance(plan["references"], list)
        or len(plan["references"]) != 2
    ):
        raise IdentityEvidenceError("invalid_private_holdings_reference_plan")
    index = cache.get(f"https://data.sec.gov/submissions/CIK{plan['manager_cik']}.json")
    if digest(index) != plan["manager_index_sha256"]:
        raise IdentityEvidenceError("holdings_manager_index_digest_mismatch")
    value = decode_json(index)
    if str(value.get("cik", "")).zfill(10) != plan["manager_cik"]:
        raise IdentityEvidenceError("holdings_manager_index_identity_mismatch")
    recent = value["filings"]["recent"]
    keys = {
        "accessionNumber",
        "form",
        "acceptanceDateTime",
        "filingDate",
        "reportDate",
        "primaryDocument",
    }
    if (
        any(not isinstance(recent.get(k), list) for k in keys)
        or len({len(recent[k]) for k in keys}) != 1
    ):
        raise IdentityEvidenceError("invalid_holdings_manager_index_columns")
    if not 1 <= len(recent["form"]) <= 20000:
        raise IdentityEvidenceError("holdings_manager_index_row_bound")
    rows = [{k: recent[k][i] for k in keys} for i in range(len(recent["form"]))]
    periods = []
    for ref in plan["references"]:
        if (
            not isinstance(ref, dict)
            or set(ref) != {"manager_cik", "metadata", "information_filename", "document_sha256"}
            or ref["manager_cik"] != plan["manager_cik"]
            or not isinstance(ref["metadata"], dict)
            or set(ref["metadata"]) != keys
            or not isinstance(ref["information_filename"], str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.xml", ref["information_filename"])
            is None
            or not isinstance(ref["document_sha256"], str)
            or re.fullmatch(r"[a-f0-9]{64}", ref["document_sha256"]) is None
        ):
            raise IdentityEvidenceError("invalid_holdings_source_reference")
        metadata = ref["metadata"]
        if sum(row == metadata for row in rows) != 1 or metadata["form"] != "13F-HR":
            raise IdentityEvidenceError("holdings_reference_not_in_bound_manager_index")
        date = datetime.strptime(metadata["reportDate"], "%Y-%m-%d").date()
        if (date.month, date.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)} or not (
            cutoff.date() - timedelta(days=730) <= date < cutoff.date()
        ):
            raise IdentityEvidenceError("holdings_reference_period_outside_bound")
        # Current index bytes are discovery evidence, not a historical universe.
        # Unread archive coverage and pre-cutoff amendments cannot be ignored.
        if any(
            archive["filingTo"] >= metadata["reportDate"]
            for archive in value["filings"].get("files", [])
        ):
            raise IdentityEvidenceError("holdings_reference_history_requires_unread_archive")
        competing = [
            row
            for row in rows
            if row["form"] in {"13F-HR", "13F-HR/A"}
            and row["reportDate"] == metadata["reportDate"]
            and instant(row["acceptanceDateTime"]) <= cutoff
        ]
        if competing != [metadata]:
            raise IdentityEvidenceError("ambiguous_or_amended_holdings_reference_period")
        source = cache.get(
            filing_url(
                {
                    "cik": ref["manager_cik"],
                    "accessionNumber": metadata["accessionNumber"],
                    "primaryDocument": metadata["accessionNumber"] + ".txt",
                }
            )
        )
        if digest(source) != ref["document_sha256"]:
            raise IdentityEvidenceError("holdings_reference_source_digest_mismatch")
        periods.append(date)
    if periods != sorted(set(periods)):
        raise IdentityEvidenceError("holdings_reference_periods_not_distinct_and_ordered")
    return plan["references"]


def candidate_supplement(
    member: dict[str, Any],
    references: list[dict[str, Any]],
    cache: SourceCache,
    retained: RetainedCheckpoint,
    cutoff: datetime,
) -> dict[str, Any] | None:
    cik = member["cik"]
    rows = submissions(cik, cache, retained, cutoff)
    periodic = None
    for row in choose_filings(rows, PERIODIC_FORMS, cutoff, 4):
        body = retained.filing(row)
        from_retained = body is not None
        if body is None:
            body = cache.get(filing_url(row))
        try:
            periodic = periodic_anchor(
                body,
                row,
                cik=cik,
                symbol=member["symbol_at_snapshot"],
                cutoff=cutoff,
                maximum_document_bytes=MAX_RETAINED_DOCUMENT_BYTES
                if from_retained
                else MAX_DOCUMENT_BYTES,
            )
            break
        except IdentityEvidenceError:
            # Unsupported cover delegation is not guessed by this supplement.
            continue
    if periodic is None or class_signature(periodic["security_title"]) != (
        "common",
        "",
        "nonvoting",
    ):
        return None
    candidates = []
    for row in ownership_candidates(rows, cik, cache, retained, cutoff):
        body = cache.get(filing_url({**row, "primaryDocument": row["accessionNumber"] + ".txt"}))
        try:
            raw_name, subject = _ownership_subject_name(body, row, cik)
        except IdentityEvidenceError as error:
            if str(error) == "ownership_subject_issuer_mismatch":
                continue
            return None
        try:
            own = ownership_identity(body, row, cik=cik, cutoff=cutoff)
            issuer_name = raw_name.decode("utf-8")
            if class_signature(own["security_title"]) != ("common", "", ""):
                return None
        except (IdentityEvidenceError, UnicodeDecodeError):
            return None
        candidates.append((own, subject, issuer_name))
    if (
        not candidates
        or len({(own["cusip"], normalized_name(name)) for own, _, name in candidates}) != 1
    ):
        return None
    own, subject, name = max(
        candidates, key=lambda item: (instant(item[0]["accepted_at"]), item[0]["accession"])
    )
    proofs = []
    for ref in references:
        metadata = ref["metadata"]
        body = cache.get(
            filing_url(
                {
                    "cik": ref["manager_cik"],
                    "accessionNumber": metadata["accessionNumber"],
                    "primaryDocument": metadata["accessionNumber"] + ".txt",
                }
            )
        )
        try:
            proofs.append(
                filed_nonvoting_class(
                    body, ref, issuer_name=name, cusip=own["cusip"], cutoff=cutoff
                )
            )
        except IdentityEvidenceError:
            return None
    try:
        anchor = join_identity(periodic, qualify_nonvoting_ownership(periodic, own, proofs))
    except IdentityEvidenceError:
        return None
    anchor["ownership_subject_evidence"] = subject
    anchor["holdings_class_reference_evidence"] = proofs
    anchor["holdings_candidate_commitments"] = sorted(
        [
            {"accession": item["accession"], "document_sha256": item["document_sha256"]}
            for item, _, _ in candidates
        ],
        key=lambda item: item["accession"],
    )
    anchor["identity_basis"] = (
        "authenticated_SEC_issuer_CUSIP_and_two_pre_ownership_filed_nonvoting_class_references"
    )
    return anchor


def recompute(
    directory: Path, baseline_name: str, plan_name: str, source_capture: Path, checkpoint: Path
) -> dict[str, Any]:
    before = algorithms()
    baseline_proof = baseline_verifier.replay(directory, baseline_name, source_capture, checkpoint)
    if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,180}\.json", plan_name) is None:
        raise IdentityEvidenceError("invalid_holdings_plan_filename")
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        baseline_raw = _read_private_file(fd, baseline_name, 32 * 1024**2)
        baseline = decode_json(baseline_raw)
        raw = _read_private_file(fd, plan_name, 128 * 1024)
        cache = SourceCache(fd, None, 1)
        cutoff = instant(baseline["binding"]["cutoff"])
        references = reference_plan(raw, cache, cutoff)
        retained = RetainedCheckpoint(checkpoint)
        members = {m["snapshot_member_key"]: m for m in baseline["binding"]["members"]}
        results = []
        added = 0
        for original in baseline["results"]:
            anchor = None
            if original["status"] == "ownership_identity_unresolved":
                anchor = candidate_supplement(
                    members[original["snapshot_member_key"]], references, cache, retained, cutoff
                )
            if anchor is None:
                results.append(original)
            else:
                results.append(
                    {
                        "snapshot_member_key": original["snapshot_member_key"],
                        "status": "pre_cutoff_SEC_identity_anchored",
                        "anchor": anchor,
                    }
                )
                added += 1
        statuses = dict(Counter(row["status"] for row in results))
        result = {
            "schema_version": 1,
            "kind": "private_filed_class_identity_supplement",
            "baseline_filename": baseline_name,
            "baseline_sha256": digest(baseline_raw),
            "baseline_verification": baseline_proof,
            "binding": baseline["binding"],
            "reference_plan_filename": plan_name,
            "reference_plan_sha256": digest(raw),
            "algorithms": before,
            "source_receipts": cache.used,
            "results": results,
            "report": {
                "source_count": baseline["report"]["source_count"],
                "processed": len(results),
                "baseline_anchors": sum("anchor" in row for row in baseline["results"]),
                "anchors_added": added,
                "baseline_anchor_payloads_changed": 0,
                "statuses": statuses,
                "provider_requests": 0,
                "paid_resources": False,
                "historical_market_continuity_verified": False,
                "checkpoint_dataset_binding_verified": False,
                "current_asset_directory_used": False,
                "issue_complete": False,
            },
        }
    if before != algorithms():
        raise IdentityEvidenceError("holdings_supplement_algorithm_changed_during_run")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--baseline-result")
    parser.add_argument("--references")
    parser.add_argument("--verify", help="Replay an existing class-enrichment-SHA256.json")
    parser.add_argument("--source-capture", required=True, type=Path)
    parser.add_argument("--retained-checkpoint", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            match = re.fullmatch(r"class-enrichment-([a-f0-9]{64})\.json", args.verify)
            if match is None or args.baseline_result or args.references:
                raise IdentityEvidenceError("invalid_holdings_supplement_replay_arguments")
            with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
                raw = _read_private_file(fd, args.verify, 32 * 1024**2)
            if digest(raw) != match[1]:
                raise IdentityEvidenceError("holdings_supplement_digest_mismatch")
            stored = decode_json(raw)
            value = recompute(
                args.directory,
                stored["baseline_filename"],
                stored["reference_plan_filename"],
                args.source_capture,
                args.retained_checkpoint,
            )
            if canonical_bytes(stored) != canonical_bytes(value):
                raise IdentityEvidenceError("holdings_supplement_replay_mismatch")
            status = "offline_class_supplement_replay_verified"
        else:
            if not args.baseline_result or not args.references:
                raise IdentityEvidenceError("missing_holdings_supplement_inputs")
            value = recompute(
                args.directory,
                args.baseline_result,
                args.references,
                args.source_capture,
                args.retained_checkpoint,
            )
            raw = canonical_bytes(value) + b"\n"
            with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
                _write_new(fd, "class-enrichment-" + digest(raw) + ".json", raw)
            status = "private_class_supplement_captured"
        print(
            json.dumps(
                {"status": status, "result_sha256": digest(raw), **value["report"]}, sort_keys=True
            )
        )
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        print(json.dumps({"refused": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
