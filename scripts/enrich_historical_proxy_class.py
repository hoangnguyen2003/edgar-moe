"""Append/replay an offline issuer-proxy class supplement; never release study gates."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from edgar_moe.data.historical_identity import (
    MAX_RETAINED_DOCUMENT_BYTES,
    IdentityEvidenceError,
    instant,
    join_identity,
    periodic_anchor,
)
from edgar_moe.data.historical_proxy_class import qualify_proxy_class
from edgar_moe.data.screen_inputs import canonical_bytes, decode_json
from scripts import enrich_historical_legacy_governance as legacy
from scripts.capture_historical_security_identity import (
    ARTIFACT_ROOT,
    OWNERSHIP_FORMS,
    PERIODIC_FORMS,
    RetainedCheckpoint,
    SourceCache,
    choose_filings,
    digest,
    filing_url,
    submissions,
)
from scripts.capture_prospective_sec_index import _capture_directory, _read_private_file, _write_new


def algorithms() -> dict[str, str]:
    return {
        **legacy.algorithms(),
        "proxy_class_algorithm_sha256": digest(
            (
                Path(__file__).resolve().parents[1] / "src/edgar_moe/data/historical_proxy_class.py"
            ).read_bytes()
        ),
        "proxy_class_supplement_sha256": digest(Path(__file__).read_bytes()),
    }


def references(raw: bytes, eligible: set[str]) -> list[dict[str, str]]:
    value = decode_json(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "scope", "references"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["scope"] != "private_issuer_proxy_missing_class_only"
        or not isinstance(value["references"], list)
        or not 1 <= len(value["references"]) <= 503
    ):
        raise IdentityEvidenceError("invalid_proxy_class_reference_plan")
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
            or re.fullmatch(r"[a-f0-9]{64}", ref["proxy_sha256"]) is None
            or re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", ref["proxy_accession"]) is None
            or re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", ref["ownership_accession"]) is None
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.html?", ref["proxy_filename"])
            is None
        ):
            raise IdentityEvidenceError("invalid_or_duplicate_proxy_class_reference")
        seen.add(ref["snapshot_member_key"])
    return value["references"]


def recompute(
    directory: Path,
    baseline_name: str,
    plan_name: str,
    governing_sources: Path,
    proxy_sources: Path,
    source_capture: Path,
    checkpoint: Path,
) -> dict[str, Any]:
    before = algorithms()
    match = re.fullmatch(r"legacy-governance-([a-f0-9]{64})\.json", baseline_name)
    if match is None or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,180}\.json", plan_name) is None:
        raise IdentityEvidenceError("invalid_proxy_class_input_names")
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        baseline_raw = _read_private_file(fd, baseline_name, 32 * 1024**2)
        plan_raw = _read_private_file(fd, plan_name, 1024**2)
    if digest(baseline_raw) != match[1]:
        raise IdentityEvidenceError("proxy_class_baseline_digest_mismatch")
    base = decode_json(baseline_raw)
    replayed = legacy.recompute(
        directory,
        base["baseline_filename"],
        base["reference_plan_filename"],
        governing_sources,
        source_capture,
        checkpoint,
    )
    if canonical_bytes(base) != canonical_bytes(replayed):
        raise IdentityEvidenceError("proxy_class_baseline_replay_mismatch")
    members = {m["snapshot_member_key"]: m for m in base["binding"]["members"]}
    eligible = {
        r["snapshot_member_key"]
        for r in base["results"]
        if r["status"] == "ownership_identity_unresolved"
    }
    plan = references(plan_raw, eligible)
    retained = RetainedCheckpoint(checkpoint)
    additions = {}
    cutoff = instant(base["binding"]["cutoff"])
    with (
        _capture_directory(directory, root=ARTIFACT_ROOT) as fd,
        _capture_directory(proxy_sources, root=ARTIFACT_ROOT) as source_fd,
    ):
        cache, proxy_cache = SourceCache(fd, None, 1), SourceCache(source_fd, None, 1)
        for ref in plan:
            member = members[ref["snapshot_member_key"]]
            cik = member["cik"]
            rows = submissions(cik, cache, retained, cutoff)
            periodic = None
            for row in choose_filings(rows, PERIODIC_FORMS, cutoff, 4):
                body = retained.filing(row)
                if body is None:
                    body = cache.get(filing_url(row))
                try:
                    periodic = periodic_anchor(
                        body,
                        row,
                        cik=cik,
                        symbol=member["symbol_at_snapshot"],
                        cutoff=cutoff,
                        maximum_document_bytes=MAX_RETAINED_DOCUMENT_BYTES,
                    )
                    break
                except IdentityEvidenceError:
                    continue
            if periodic is None:
                raise IdentityEvidenceError("proxy_class_periodic_anchor_unavailable")
            choices = [
                r
                for r in rows
                if r["accessionNumber"] == ref["ownership_accession"]
                and r["form"] in OWNERSHIP_FORMS
            ]
            if len(choices) != 1:
                raise IdentityEvidenceError("proxy_class_ownership_metadata_ambiguous")
            ownership = choices[0]
            own_body = cache.get(
                filing_url({**ownership, "primaryDocument": ownership["accessionNumber"] + ".txt"})
            )
            proxy_url = filing_url(
                {
                    "cik": cik,
                    "accessionNumber": ref["proxy_accession"],
                    "primaryDocument": ref["proxy_accession"] + ".txt",
                }
            )
            proxy = proxy_cache.get(proxy_url)
            if digest(proxy) != ref["proxy_sha256"]:
                raise IdentityEvidenceError("proxy_class_proposed_source_digest_mismatch")
            qualified = qualify_proxy_class(
                periodic,
                own_body,
                ownership,
                proxy,
                proxy_accession=ref["proxy_accession"],
                proxy_filename=ref["proxy_filename"],
                cutoff=cutoff,
            )
            anchor = join_identity(periodic, qualified)
            anchor["identity_basis"] = (
                "same_SEC_subject_and_issuer_filed_exact_ownership_report_named_class_attribution"
            )
            additions[ref["snapshot_member_key"]] = anchor
        results = legacy.append_results(base, additions)
        value = {
            "schema_version": 1,
            "kind": "private_issuer_proxy_missing_class_supplement",
            "baseline_filename": baseline_name,
            "baseline_sha256": digest(baseline_raw),
            "binding": base["binding"],
            "reference_plan_filename": plan_name,
            "reference_plan_sha256": digest(plan_raw),
            "algorithms": before,
            "source_receipts": cache.used,
            "proxy_source_receipts": proxy_cache.used,
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
                "publication_gate_released": False,
                "issue_complete": False,
            },
        }
    if algorithms() != before:
        raise IdentityEvidenceError("proxy_class_algorithm_changed_during_replay")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in [
        "directory",
        "governing-sources",
        "proxy-sources",
        "source-capture",
        "retained-checkpoint",
    ]:
        parser.add_argument("--" + name, required=True, type=Path)
    for name in ["baseline", "references", "verify"]:
        parser.add_argument("--" + name)
    args = parser.parse_args()
    try:
        stored = None
        if args.verify:
            match = re.fullmatch(r"proxy-class-([a-f0-9]{64})\.json", args.verify)
            if match is None or args.baseline or args.references:
                raise IdentityEvidenceError("invalid_proxy_class_replay_arguments")
            with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
                raw = _read_private_file(fd, args.verify, 32 * 1024**2)
            if digest(raw) != match[1]:
                raise IdentityEvidenceError("proxy_class_result_digest_mismatch")
            stored = decode_json(raw)
            baseline, plan = stored["baseline_filename"], stored["reference_plan_filename"]
        else:
            if not args.baseline or not args.references:
                raise IdentityEvidenceError("missing_proxy_class_supplement_inputs")
            baseline, plan = args.baseline, args.references
        value = recompute(
            args.directory,
            baseline,
            plan,
            args.governing_sources,
            args.proxy_sources,
            args.source_capture,
            args.retained_checkpoint,
        )
        if stored is not None:
            if canonical_bytes(value) != canonical_bytes(stored):
                raise IdentityEvidenceError("proxy_class_result_replay_mismatch")
            status = "offline_proxy_class_supplement_replay_verified"
        else:
            raw = canonical_bytes(value) + b"\n"
            with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
                _write_new(fd, "proxy-class-" + digest(raw) + ".json", raw)
            status = "private_proxy_class_supplement_captured"
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
