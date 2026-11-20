"""Append a replayable FDIC/SEC equity identity corroborated by a public prospectus."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from edgar_moe.data.historical_bank_filings import filing_metadata, periodic_report, select_filing
from edgar_moe.data.historical_fdic_equity import filed_equity_reference
from edgar_moe.data.historical_governance import _par_value
from edgar_moe.data.historical_identity import (
    IdentityEvidenceError,
    class_signature,
    instant,
    join_identity,
    ownership_identity,
    ownership_subject,
)
from edgar_moe.data.screen_inputs import canonical_bytes, decode_json
from scripts import enrich_historical_capital_stock_tags as previous
from scripts.capture_historical_security_identity import (
    ARTIFACT_ROOT,
    RetainedCheckpoint,
    SourceCache,
    digest,
    filing_url,
    ownership_candidates,
    submissions,
)
from scripts.capture_prospective_sec_index import _capture_directory, _read_private_file, _write_new
from scripts.enrich_historical_bank_filings import FDIC_API, FDICCache, append_results

KIND = "private_FDIC_SEC_prospectus_equity_supplement"
PREFIX = "fdic-listed-equity"
PLAN_SCOPE = "private_FDIC_SEC_prospectus_equity_only"
SOURCE_ARGS = (*previous.SOURCE_ARGS, "listed-equity-sources")


def algorithms() -> dict[str, str]:
    return {
        **previous.algorithms(),
        "fdic_equity_prospectus_algorithm_sha256": digest(
            (
                Path(__file__).resolve().parents[1] / "src/edgar_moe/data/historical_fdic_equity.py"
            ).read_bytes()
        ),
        "fdic_equity_supplement_sha256": digest(Path(__file__).read_bytes()),
    }


def reference(raw: bytes, eligible: set[str]) -> dict[str, Any]:
    plan = decode_json(raw)
    if (
        not isinstance(plan, dict)
        or set(plan)
        != {"schema_version", "scope", "snapshot_member_key", "certificate", "prospectus"}
        or type(plan["schema_version"]) is not int
        or plan["schema_version"] != 1
        or plan["scope"] != PLAN_SCOPE
        or not isinstance(plan["snapshot_member_key"], str)
        or plan["snapshot_member_key"] not in eligible
        or type(plan["certificate"]) is not int
        or not 0 < plan["certificate"] < 1000000
    ):
        raise IdentityEvidenceError("invalid_fdic_equity_plan")
    ref = plan["prospectus"]
    if (
        not isinstance(ref, dict)
        or set(ref) != {"filer_cik", "accession", "filename"}
        or not all(isinstance(value, str) for value in ref.values())
        or re.fullmatch(r"[0-9]{10}", ref["filer_cik"]) is None
        or int(ref["filer_cik"]) == 0
        or re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", ref["accession"]) is None
        or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,180}\.html?", ref["filename"]) is None
    ):
        raise IdentityEvidenceError("invalid_fdic_equity_prospectus_reference")
    return plan


def candidate(
    member: dict[str, Any],
    plan: dict[str, Any],
    cache: SourceCache,
    fdic: FDICCache,
    owners: SourceCache,
    prospectus: SourceCache,
    retained: RetainedCheckpoint,
    cutoff: datetime,
) -> dict[str, Any]:
    cik = member["cik"]
    cert = plan["certificate"]
    index = fdic.get(FDIC_API + "cert/" + str(cert))
    selected = select_filing(index, cert, cutoff)
    detail_url = FDIC_API + str(selected["instFlngId"])
    meta = filing_metadata(selected, fdic.get(detail_url), cutoff)
    periodic = periodic_report(
        fdic.get(detail_url + "/attachment/" + str(meta["attachment_id"])),
        meta,
        cik=cik,
        symbol=member["symbol_at_snapshot"],
        cutoff=cutoff,
    )
    ref = plan["prospectus"]
    offer_url = filing_url(
        {
            "cik": ref["filer_cik"],
            "accessionNumber": ref["accession"],
            "primaryDocument": ref["accession"] + ".txt",
        }
    )
    proof = filed_equity_reference(prospectus.get(offer_url), ref, periodic, cutoff=cutoff)
    reports = []
    rows = submissions(cik, cache, retained, cutoff)
    for row in ownership_candidates(rows, cik, cache, retained, cutoff):
        raw = owners.get(filing_url(row | {"primaryDocument": row["accessionNumber"] + ".txt"}))
        subject = ownership_subject(raw, cik=cik, accession=row["accessionNumber"])
        own = ownership_identity(raw, row, cik=cik, cutoff=cutoff)
        if (
            own["cusip"] != proof["cusip"]
            or class_signature(own["security_title"]) != ("common", "", "")
            or (
                "$" in own["security_title"]
                and _par_value(own["security_title"]) != _par_value(periodic["security_title"])
            )
        ):
            raise IdentityEvidenceError("FDIC_equity_conflicting_SEC_ownership")
        reports.append(own | {"ownership_subject_evidence": subject})
    if len(reports) < 2:
        raise IdentityEvidenceError("FDIC_equity_insufficient_SEC_ownership")
    own = max(reports, key=lambda report: (instant(report["available_by"]), report["accession"]))
    anchor = join_identity(periodic, own)
    return anchor | {
        "available_by": max(
            instant(anchor["available_by"]), instant(proof["available_by"])
        ).isoformat(),
        "identity_basis": "FDIC_periodic_SEC_prospectus_certificate_and_SEC_ownership_subject",
        "FDIC_index_sha256": digest(index),
        "prospectus_evidence": proof,
        "ownership_subject_EIN_used_as_join_key": False,
        "corroborating_ownership_commitments": sorted(
            (
                {"accession": report["accession"], "document_sha256": report["document_sha256"]}
                for report in reports
            ),
            key=lambda row: row["accession"],
        ),
    }


def recompute(
    directory: Path, baseline_name: str, plan_name: str, *sources: Path
) -> dict[str, Any]:
    before = algorithms()
    match = re.fullmatch(r"capital-stock-tags-([a-f0-9]{64})\.json", baseline_name)
    if match is None or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,180}\.json", plan_name) is None:
        raise IdentityEvidenceError("invalid_fdic_equity_input_names")
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        raw = _read_private_file(fd, baseline_name, 32 * 1024**2)
        plan_raw = _read_private_file(fd, plan_name, 1024**2)
    if digest(raw) != match[1]:
        raise IdentityEvidenceError("fdic_equity_baseline_digest_mismatch")
    base = decode_json(raw)
    if not isinstance(base, dict) or base.get("kind") != previous.KIND:
        raise IdentityEvidenceError("fdic_equity_baseline_kind_mismatch")
    parent_sources = sources[:-1]
    parent = previous.recompute(
        directory, base["baseline_filename"], base["reference_plan_filename"], *parent_sources
    )
    if canonical_bytes(parent) != canonical_bytes(base):
        raise IdentityEvidenceError("fdic_equity_parent_replay_mismatch")
    eligible = {
        row["snapshot_member_key"]
        for row in base["results"]
        if row["status"] == "periodic_anchor_unresolved"
    }
    plan = reference(plan_raw, eligible)
    key = plan["snapshot_member_key"]
    member = next(row for row in base["binding"]["members"] if row["snapshot_member_key"] == key)
    bank_sources, bank_ownership_sources = sources[3:5]
    with (
        _capture_directory(directory, root=ARTIFACT_ROOT) as fd,
        _capture_directory(bank_sources, root=ARTIFACT_ROOT) as banks_fd,
        _capture_directory(bank_ownership_sources, root=ARTIFACT_ROOT) as owners_fd,
        _capture_directory(sources[-1], root=ARTIFACT_ROOT) as offers_fd,
    ):
        cache = SourceCache(fd, None, 1)
        banks = FDICCache(banks_fd)
        owners = SourceCache(owners_fd, None, 1)
        prospectus = SourceCache(offers_fd, None, 1)
        qualified = candidate(
            member,
            plan,
            cache,
            banks,
            owners,
            prospectus,
            RetainedCheckpoint(parent_sources[-1]),
            instant(base["binding"]["cutoff"]),
        )
        results = append_results(base, {key: qualified})
    if algorithms() != before:
        raise IdentityEvidenceError("fdic_equity_algorithm_changed_during_replay")
    return {
        "schema_version": 1,
        "kind": KIND,
        "baseline_filename": baseline_name,
        "baseline_sha256": digest(raw),
        "reference_plan_filename": plan_name,
        "reference_plan_sha256": digest(plan_raw),
        "binding": base["binding"],
        "algorithms": before,
        "source_receipts": {
            "SEC_submissions": cache.used,
            "FDIC": banks.used,
            "SEC_ownership": owners.used,
            "SEC_prospectus": prospectus.used,
        },
        "results": results,
        "report": {
            **base["report"],
            "baseline_anchors": sum("anchor" in row for row in base["results"]),
            "anchors_added": 1,
            "statuses": dict(Counter(row["status"] for row in results)),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("directory", *SOURCE_ARGS):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--references", required=True)
    args = parser.parse_args()
    try:
        value = recompute(
            args.directory,
            args.baseline,
            args.references,
            *(getattr(args, name.replace("-", "_")) for name in SOURCE_ARGS),
        )
        raw = canonical_bytes(value) + b"\n"
        filename = PREFIX + "-" + digest(raw) + ".json"
        with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
            _write_new(fd, filename, raw)
        print(json.dumps({"result_filename": filename, **value["report"]}, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        print(json.dumps({"refused": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
