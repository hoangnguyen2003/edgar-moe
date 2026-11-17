"""Append/replay source-qualified FDIC bank evidence without admitting a study."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from edgar_moe.data.historical_bank_filings import (
    ANCHOR_STATUS,
    MAX_PDF_BYTES,
    decoder_identity,
    digest,
    filing_metadata,
    periodic_report,
    qualify_bank_proxy,
    select_filing,
)
from edgar_moe.data.historical_governance import _par_value
from edgar_moe.data.historical_identity import (
    IdentityEvidenceError,
    _ownership_subject_name,
    class_signature,
    instant,
    ownership_identity,
)
from edgar_moe.data.screen_inputs import canonical_bytes, decode_json
from scripts import enrich_historical_class_redesignation as previous
from scripts.capture_historical_security_identity import (
    ARTIFACT_ROOT,
    RetainedCheckpoint,
    SourceCache,
    filing_url,
    ownership_candidates,
    submissions,
)
from scripts.capture_prospective_sec_index import _capture_directory, _read_private_file, _write_new

FDIC_API = "https://securitiesfilings.fdicconnect.fdic.gov/api/instflng/"


def algorithms() -> dict[str, str]:
    return {
        **previous.algorithms(),
        "bank_filing_algorithm_sha256": digest(
            (
                Path(__file__).resolve().parents[1]
                / "src/edgar_moe/data/historical_bank_filings.py"
            ).read_bytes()
        ),
        "bank_supplement_sha256": digest(Path(__file__).read_bytes()),
        "PDF_decoder_sources_sha256": decoder_identity(),
    }


class FDICCache:
    """Bounded retained public responses only: no HTTP client or live mode."""

    def __init__(self, fd: int):
        self.fd = fd
        self.used: dict[str, Any] = {}

    def get(self, url: str) -> bytes:
        if (
            re.fullmatch(
                re.escape(FDIC_API)
                + r"(?:cert/[1-9][0-9]{0,5}|[1-9][0-9]{0,8}(?:/attachment/[1-9][0-9]{0,3})?)",
                url,
            )
            is None
        ):
            raise IdentityEvidenceError("unapproved_FDIC_source_URL")
        key = digest(url.encode())
        meta = decode_json(_read_private_file(self.fd, key + ".json", 4096))
        raw = _read_private_file(self.fd, key + ".body", MAX_PDF_BYTES)
        if (
            not isinstance(meta, dict)
            or meta.get("url") != url
            or meta.get("status_code") != 200
            or meta.get("TLS_verified") is not True
            or meta.get("automatic_retries") != 0
            or meta.get("location") is not None
            or meta.get("sha256") != digest(raw)
            or meta.get("bytes") != len(raw)
        ):
            raise IdentityEvidenceError("FDIC_source_cache_tampered_or_unqualified")
        instant(meta.get("retrieved_at"))
        self.used[key] = meta
        return raw


def references(raw: bytes, eligible: set[str]) -> list[dict[str, Any]]:
    value = decode_json(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "scope", "references"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["scope"] != "private_FDIC_periodic_exact_issuer_proxy_citation_only"
        or not isinstance(value["references"], list)
        or not 1 <= len(value["references"]) <= 503
    ):
        raise IdentityEvidenceError("invalid_bank_filing_reference_plan")
    seen = set()
    for ref in value["references"]:
        if (
            not isinstance(ref, dict)
            or set(ref)
            != {"snapshot_member_key", "certificate", "proxy_filing_id", "ownership_accession"}
            or ref.get("snapshot_member_key") not in eligible
            or ref["snapshot_member_key"] in seen
            or type(ref.get("certificate")) is not int
            or not 0 < ref["certificate"] < 1000000
            or type(ref.get("proxy_filing_id")) is not int
            or not 0 < ref["proxy_filing_id"] < 10**9
            or not isinstance(ref.get("ownership_accession"), str)
            or re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", ref["ownership_accession"]) is None
        ):
            raise IdentityEvidenceError("invalid_or_duplicate_bank_filing_reference")
        seen.add(ref["snapshot_member_key"])
    return value["references"]


def candidate(
    member: dict[str, Any],
    ref: dict[str, Any],
    cache: SourceCache,
    fdic: FDICCache,
    owners: SourceCache,
    retained: RetainedCheckpoint,
    cutoff: datetime,
) -> dict[str, Any]:
    index_raw = fdic.get(FDIC_API + "cert/" + str(ref["certificate"]))
    selected = select_filing(index_raw, ref["certificate"], cutoff)
    detail = fdic.get(FDIC_API + str(selected["instFlngId"]))
    meta = filing_metadata(selected, detail, cutoff)
    pdf = fdic.get(FDIC_API + str(meta["filing_id"]) + "/attachment/" + str(meta["attachment_id"]))
    periodic = periodic_report(
        pdf, meta, cik=member["cik"], symbol=member["symbol_at_snapshot"], cutoff=cutoff
    )
    proxy_rows = [r for r in decode_json(index_raw) if r["instFlngId"] == ref["proxy_filing_id"]]
    if len(proxy_rows) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_FDIC_proxy_index_entry")
    proxy_meta = filing_metadata(
        proxy_rows[0], fdic.get(FDIC_API + str(ref["proxy_filing_id"])), cutoff
    )
    proxy = fdic.get(
        FDIC_API + str(proxy_meta["filing_id"]) + "/attachment/" + str(proxy_meta["attachment_id"])
    )
    rows = submissions(member["cik"], cache, retained, cutoff)
    observed = []
    qualified = []
    for row in ownership_candidates(rows, member["cik"], cache, retained, cutoff):
        raw = owners.get(filing_url(row | {"primaryDocument": row["accessionNumber"] + ".txt"}))
        _ownership_subject_name(raw, row, member["cik"])
        own = ownership_identity(raw, row, cik=member["cik"], cutoff=cutoff)
        if class_signature(own["security_title"]) != class_signature(periodic["security_title"]):
            raise IdentityEvidenceError("bank_corrobating_ownership_class_conflict")
        if "$" in own["security_title"] and _par_value(own["security_title"]) != _par_value(
            periodic["security_title"]
        ):
            raise IdentityEvidenceError("bank_corrobating_ownership_par_conflict")
        observed.append(
            {
                "accession": own["accession"],
                "document_sha256": own["document_sha256"],
                "cusip": own["cusip"],
            }
        )
        if row["accessionNumber"] == ref["ownership_accession"]:
            qualified.append(
                qualify_bank_proxy(periodic, raw, row, proxy, proxy_meta, cutoff=cutoff)
            )
    if len(qualified) != 1 or len({r["cusip"] for r in observed}) != 1:
        raise IdentityEvidenceError("bank_qualified_or_corroborating_ownership_ambiguous")
    return {
        **qualified[0],
        "FDIC_index_sha256": digest(index_raw),
        "corroborating_ownership_commitments": sorted(observed, key=lambda r: r["accession"]),
    }


def append_results(base: dict[str, Any], additions: dict[str, Any]) -> list[dict[str, Any]]:
    keys = [r["snapshot_member_key"] for r in base["results"]]
    if len(set(keys)) != len(keys) or set(additions) - set(keys):
        raise IdentityEvidenceError("bank_supplement_member_set_changed")
    result = []
    for original in base["results"]:
        key = original["snapshot_member_key"]
        if key not in additions:
            result.append(original)
        elif original["status"] != "periodic_anchor_unresolved" or "anchor" in original:
            raise IdentityEvidenceError("bank_supplement_existing_anchor_or_ownership_override")
        else:
            result.append(
                {"snapshot_member_key": key, "status": ANCHOR_STATUS, "anchor": additions[key]}
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
    source_capture: Path,
    checkpoint: Path,
) -> dict[str, Any]:
    before = algorithms()
    match = re.fullmatch(r"class-redesignation-([a-f0-9]{64})\.json", baseline_name)
    if match is None or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,180}\.json", plan_name) is None:
        raise IdentityEvidenceError("invalid_bank_supplement_input_names")
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        raw = _read_private_file(fd, baseline_name, 32 * 1024**2)
        plan_raw = _read_private_file(fd, plan_name, 1024**2)
    if digest(raw) != match[1]:
        raise IdentityEvidenceError("bank_supplement_baseline_digest_mismatch")
    base = decode_json(raw)
    if (
        not isinstance(base, dict)
        or base.get("kind") != "private_charter_class_redesignation_supplement"
    ):
        raise IdentityEvidenceError("unsupported_bank_supplement_baseline_kind")
    parent = previous.recompute(
        directory,
        base["baseline_filename"],
        base["reference_plan_filename"],
        governing_sources,
        proxy_sources,
        redesignation_sources,
        source_capture,
        checkpoint,
    )
    if canonical_bytes(parent) != canonical_bytes(base):
        raise IdentityEvidenceError("bank_supplement_parent_replay_mismatch")
    eligible = {
        r["snapshot_member_key"]
        for r in base["results"]
        if r["status"] == "periodic_anchor_unresolved"
    }
    plan = references(plan_raw, eligible)
    members = {r["snapshot_member_key"]: r for r in base["binding"]["members"]}
    retained = RetainedCheckpoint(checkpoint)
    with (
        _capture_directory(directory, root=ARTIFACT_ROOT) as fd,
        _capture_directory(bank_sources, root=ARTIFACT_ROOT) as bank_fd,
        _capture_directory(bank_ownership_sources, root=ARTIFACT_ROOT) as own_fd,
    ):
        cache, fdic, owners = (
            SourceCache(fd, None, 1),
            FDICCache(bank_fd),
            SourceCache(own_fd, None, 1),
        )
        additions = {
            r["snapshot_member_key"]: candidate(
                members[r["snapshot_member_key"]],
                r,
                cache,
                fdic,
                owners,
                retained,
                instant(base["binding"]["cutoff"]),
            )
            for r in plan
        }
        results = append_results(base, additions)
        result = {
            "schema_version": 1,
            "kind": "private_FDIC_periodic_SEC_ownership_supplement",
            "baseline_filename": baseline_name,
            "baseline_sha256": digest(raw),
            "reference_plan_filename": plan_name,
            "reference_plan_sha256": digest(plan_raw),
            "binding": base["binding"],
            "algorithms": before,
            "source_receipts": cache.used,
            "FDIC_source_receipts": fdic.used,
            "ownership_source_receipts": owners.used,
            "results": results,
            "report": {
                "source_count": base["report"]["source_count"],
                "processed": len(results),
                "baseline_anchors": sum("anchor" in r for r in base["results"]),
                "anchors_added": len(additions),
                "baseline_anchor_payloads_changed": 0,
                "statuses": dict(Counter(r["status"] for r in results)),
                "subject_tax_identifier_used_as_join_key": False,
                "exact_issuer_filed_ownership_citation_required": True,
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
        raise IdentityEvidenceError("bank_algorithm_changed_during_replay")
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    for name in [
        "directory",
        "governing-sources",
        "proxy-sources",
        "redesignation-sources",
        "bank-sources",
        "bank-ownership-sources",
        "source-capture",
        "retained-checkpoint",
    ]:
        p.add_argument("--" + name, type=Path, required=True)
    for name in ["baseline", "references", "verify"]:
        p.add_argument("--" + name)
    a = p.parse_args()
    try:
        stored = None
        if a.verify:
            match = re.fullmatch(r"bank-filings-([a-f0-9]{64})\.json", a.verify)
            if match is None or a.baseline or a.references:
                raise IdentityEvidenceError("invalid_bank_replay_arguments")
            with _capture_directory(a.directory, root=ARTIFACT_ROOT) as fd:
                raw = _read_private_file(fd, a.verify, 32 * 1024**2)
            if digest(raw) != match[1]:
                raise IdentityEvidenceError("bank_result_digest_mismatch")
            stored = decode_json(raw)
            baseline, plan = stored["baseline_filename"], stored["reference_plan_filename"]
        else:
            if not a.baseline or not a.references:
                raise IdentityEvidenceError("missing_bank_supplement_inputs")
            baseline, plan = a.baseline, a.references
        value = recompute(
            a.directory,
            baseline,
            plan,
            a.governing_sources,
            a.proxy_sources,
            a.redesignation_sources,
            a.bank_sources,
            a.bank_ownership_sources,
            a.source_capture,
            a.retained_checkpoint,
        )
        if stored is not None:
            if canonical_bytes(stored) != canonical_bytes(value):
                raise IdentityEvidenceError("bank_result_replay_mismatch")
            status = "offline_bank_filing_replay_verified"
        else:
            raw = canonical_bytes(value) + b"\n"
            with _capture_directory(a.directory, root=ARTIFACT_ROOT) as fd:
                _write_new(fd, "bank-filings-" + digest(raw) + ".json", raw)
            status = "private_bank_filing_supplement_captured"
        print(
            json.dumps(
                {"status": status, "result_sha256": digest(raw), **value["report"]}, sort_keys=True
            )
        )
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, UnicodeDecodeError) as e:
        print(json.dumps({"refused": type(e).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
