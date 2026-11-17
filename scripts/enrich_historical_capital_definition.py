"""Append/replay an explicit issuer capital-stock definition from private cached sources."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from edgar_moe.data.historical_capital_definition import qualify_capital_definition
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
from scripts import enrich_historical_bank_filings as previous
from scripts.capture_historical_security_identity import (
    ARTIFACT_ROOT,
    OWNERSHIP_FORMS,
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

KIND = "private_issuer_capital_definition_supplement"


def algorithms() -> dict[str, str]:
    return {
        **previous.algorithms(),
        "capital_definition_algorithm_sha256": digest(
            (
                Path(__file__).resolve().parents[1]
                / "src/edgar_moe/data/historical_capital_definition.py"
            ).read_bytes()
        ),
        "capital_definition_supplement_sha256": digest(Path(__file__).read_bytes()),
    }


def references(raw: bytes, eligible: set[str]) -> list[dict[str, str]]:
    value = decode_json(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "scope", "references"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["scope"] != "private_explicit_capital_definition_and_exact_ownership_citation_only"
        or not isinstance(value["references"], list)
        or not 1 <= len(value["references"]) <= 503
    ):
        raise IdentityEvidenceError("invalid_capital_definition_plan")
    seen = set()
    for ref in value["references"]:
        if (
            not isinstance(ref, dict)
            or set(ref)
            != {
                "snapshot_member_key",
                "proxy_accession",
                "proxy_filename",
                "proxy_sha256",
                "ownership_accession",
            }
            or not all(isinstance(v, str) for v in ref.values())
            or ref["snapshot_member_key"] not in eligible
            or ref["snapshot_member_key"] in seen
            or any(
                re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", ref[k]) is None
                for k in ["proxy_accession", "ownership_accession"]
            )
            or re.fullmatch(r"[a-f0-9]{64}", ref["proxy_sha256"]) is None
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.html?", ref["proxy_filename"])
            is None
        ):
            raise IdentityEvidenceError("invalid_or_duplicate_capital_definition_reference")
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
    periodic = None
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
            break
        except IdentityEvidenceError:
            continue
    if periodic is None or class_signature(periodic["security_title"]) != ("capital", "", ""):
        raise IdentityEvidenceError("missing_capital_definition_periodic_registration")
    choices = [
        r
        for r in rows
        if r["accessionNumber"] == ref["ownership_accession"] and r["form"] in OWNERSHIP_FORMS
    ]
    if len(choices) != 1:
        raise IdentityEvidenceError("ambiguous_capital_definition_ownership_reference")
    row = choices[0]
    own_raw = cache.get(filing_url({**row, "primaryDocument": row["accessionNumber"] + ".txt"}))
    proxy_raw = source.get(
        filing_url(
            {
                "cik": cik,
                "accessionNumber": ref["proxy_accession"],
                "primaryDocument": ref["proxy_accession"] + ".txt",
            }
        )
    )
    if digest(proxy_raw) != ref["proxy_sha256"]:
        raise IdentityEvidenceError("capital_definition_source_digest_mismatch")
    own = qualify_capital_definition(
        periodic,
        own_raw,
        row,
        proxy_raw,
        proxy_accession=ref["proxy_accession"],
        proxy_filename=ref["proxy_filename"],
        cutoff=cutoff,
    )
    commitments = []
    refusals: Counter[str] = Counter()
    for other in ownership_candidates(rows, cik, cache, retained, cutoff):
        body = cache.get(
            filing_url({**other, "primaryDocument": other["accessionNumber"] + ".txt"})
        )
        try:
            _ownership_subject_name(body, other, cik)
            report = ownership_identity(body, other, cik=cik, cutoff=cutoff)
        except IdentityEvidenceError as error:
            refusals[str(error)] += 1
            continue
        if (
            class_signature(report["security_title"]) != ("common", "", "")
            or (
                "$" in report["security_title"]
                and _par_value(report["security_title"]) != _par_value(periodic["security_title"])
            )
            or report["cusip"] != own["cusip"]
        ):
            raise IdentityEvidenceError("capital_definition_corroborating_ownership_conflict")
        commitments.append(
            {"accession": report["accession"], "document_sha256": report["document_sha256"]}
        )
    if len([c for c in commitments if c["accession"] == ref["ownership_accession"]]) != 1:
        raise IdentityEvidenceError("capital_definition_cited_ownership_not_discovered")
    result = join_identity(periodic, own)
    result.update(
        identity_basis="same_SEC_subject_explicit_issuer_common_capital_definition_and_exact_filed_ownership_citation",
        corroborating_ownership_commitments=sorted(commitments, key=lambda r: r["accession"]),
        discovery_refusals=dict(refusals),
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
    match = re.fullmatch(r"bank-filings-([a-f0-9]{64})\.json", baseline_name)
    if match is None or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,180}\.json", plan_name) is None:
        raise IdentityEvidenceError("invalid_capital_definition_input_names")
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        raw = _read_private_file(fd, baseline_name, 32 * 1024**2)
        plan_raw = _read_private_file(fd, plan_name, 1024**2)
    if digest(raw) != match[1]:
        raise IdentityEvidenceError("capital_definition_baseline_digest_mismatch")
    base = decode_json(raw)
    if base.get("kind") != "private_FDIC_periodic_SEC_ownership_supplement":
        raise IdentityEvidenceError("capital_definition_baseline_kind_mismatch")
    replayed = previous.recompute(
        directory,
        base["baseline_filename"],
        base["reference_plan_filename"],
        governing_sources,
        proxy_sources,
        redesignation_sources,
        bank_sources,
        bank_ownership_sources,
        source_capture,
        checkpoint,
    )
    if canonical_bytes(replayed) != canonical_bytes(base):
        raise IdentityEvidenceError("capital_definition_parent_replay_mismatch")
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
        _capture_directory(capital_sources, root=ARTIFACT_ROOT) as source_fd,
    ):
        cache, source = SourceCache(fd, None, 1), SourceCache(source_fd, None, 1)
        additions = {
            r["snapshot_member_key"]: candidate(
                members[r["snapshot_member_key"]],
                r,
                cache,
                source,
                retained,
                instant(base["binding"]["cutoff"]),
            )
            for r in plan
        }
        results = append_results(base, additions)
        value = {
            "schema_version": 1,
            "kind": KIND,
            "baseline_filename": baseline_name,
            "baseline_sha256": digest(raw),
            "reference_plan_filename": plan_name,
            "reference_plan_sha256": digest(plan_raw),
            "binding": base["binding"],
            "algorithms": before,
            "source_receipts": cache.used,
            "capital_source_receipts": source.used,
            "results": results,
            "report": {
                "source_count": base["report"]["source_count"],
                "processed": len(results),
                "baseline_anchors": sum("anchor" in r for r in base["results"]),
                "anchors_added": len(additions),
                "baseline_anchor_payloads_changed": 0,
                "statuses": dict(Counter(r["status"] for r in results)),
                "generic_class_or_equity_family_aliases_added": False,
                "provider_requests": 0,
                "paid_resources": False,
                "historical_market_continuity_verified": False,
                "checkpoint_dataset_binding_verified": False,
                "publication_gate_released": False,
                "issue_complete": False,
            },
        }
    if algorithms() != before:
        raise IdentityEvidenceError("capital_definition_algorithm_changed_during_replay")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    paths = [
        "directory",
        "governing-sources",
        "proxy-sources",
        "redesignation-sources",
        "bank-sources",
        "bank-ownership-sources",
        "capital-sources",
        "source-capture",
        "retained-checkpoint",
    ]
    for name in paths:
        parser.add_argument("--" + name, required=True, type=Path)
    for name in ["baseline", "references", "verify"]:
        parser.add_argument("--" + name)
    args = parser.parse_args()
    try:
        stored = None
        if args.verify:
            match = re.fullmatch(r"capital-definition-([a-f0-9]{64})\.json", args.verify)
            if match is None or args.baseline or args.references:
                raise IdentityEvidenceError("invalid_capital_definition_replay_arguments")
            with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
                raw = _read_private_file(fd, args.verify, 32 * 1024**2)
            if digest(raw) != match[1]:
                raise IdentityEvidenceError("capital_definition_result_digest_mismatch")
            stored = decode_json(raw)
            baseline, plan = stored["baseline_filename"], stored["reference_plan_filename"]
        else:
            if not args.baseline or not args.references:
                raise IdentityEvidenceError("missing_capital_definition_inputs")
            baseline, plan = args.baseline, args.references
        value = recompute(
            args.directory, baseline, plan, *(getattr(args, n.replace("-", "_")) for n in paths[1:])
        )
        if stored is not None:
            if canonical_bytes(value) != canonical_bytes(stored):
                raise IdentityEvidenceError("capital_definition_result_replay_mismatch")
            status = "offline_capital_definition_replay_verified"
        else:
            raw = canonical_bytes(value) + b"\n"
            with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
                _write_new(fd, "capital-definition-" + digest(raw) + ".json", raw)
            status = "private_capital_definition_supplement_captured"
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
