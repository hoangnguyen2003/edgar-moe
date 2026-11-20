"""Append a historical listed-share identity from a filed issuer offering."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from edgar_moe.data.historical_identity import (
    MAX_RETAINED_DOCUMENT_BYTES,
    IdentityEvidenceError,
    _soup,
    class_signature,
    instant,
    join_identity,
    ownership_identity,
    ownership_subject,
    periodic_anchor,
)
from edgar_moe.data.historical_offering_identity import ordinary_share_offering
from edgar_moe.data.screen_inputs import canonical_bytes, decode_json
from scripts import enrich_historical_vote_identity as previous
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
from scripts.enrich_historical_listed_isin import ACCESSION, registration_name

KIND = "private_SEC_issuer_ordinary_share_offering_supplement"
PREFIX = "issuer-offering"
PLAN_SCOPE = "private_SEC_issuer_ordinary_share_offering_only"
SOURCE_ARGS = (*previous.SOURCE_ARGS, "offering-identity-sources")


def algorithms() -> dict[str, str]:
    algorithm = (
        Path(__file__).resolve().parents[1] / "src/edgar_moe/data/historical_offering_identity.py"
    )
    return {
        **previous.algorithms(),
        "offering_identity_algorithm_sha256": digest(algorithm.read_bytes()),
        "offering_identity_supplement_sha256": digest(Path(__file__).read_bytes()),
    }


def reference(raw: bytes, eligible: set[str]) -> dict[str, Any]:
    plan = decode_json(raw)
    if (
        not isinstance(plan, dict)
        or set(plan) != {"schema_version", "scope", "snapshot_member_key", "issuer_accession"}
        or type(plan["schema_version"]) is not int
        or plan["schema_version"] != 1
        or plan["scope"] != PLAN_SCOPE
        or not isinstance(plan["snapshot_member_key"], str)
        or plan["snapshot_member_key"] not in eligible
        or not isinstance(plan["issuer_accession"], str)
        or re.fullmatch(ACCESSION, plan["issuer_accession"]) is None
    ):
        raise IdentityEvidenceError("invalid_offering_identity_plan")
    return plan


def candidate(
    member: dict[str, Any],
    plan: dict[str, Any],
    cache: SourceCache,
    sources: SourceCache,
    retained: RetainedCheckpoint,
    cutoff: datetime,
) -> dict[str, Any]:
    cik, symbol = member["cik"], member["symbol_at_snapshot"]
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
                symbol=symbol,
                cutoff=cutoff,
                maximum_document_bytes=MAX_RETAINED_DOCUMENT_BYTES,
            )
        except IdentityEvidenceError:
            continue
        break
    else:
        raise IdentityEvidenceError("offering_identity_periodic_registration_missing")
    if class_signature(periodic["security_title"]) != ("ordinary", "", ""):
        raise IdentityEvidenceError("offering_identity_wrong_periodic_class")
    issuer_name = registration_name(raw, cik)
    accession = plan["issuer_accession"]
    reference = {"accession": accession, "filer_cik": cik}
    evidence = ordinary_share_offering(
        sources.get(
            filing_url(
                {
                    "cik": cik,
                    "accessionNumber": accession,
                    "primaryDocument": accession + ".txt",
                }
            )
        ),
        reference,
        periodic,
        issuer_name=issuer_name,
        cutoff=cutoff,
    )
    reports = []
    refusals = []
    for row in ownership_candidates(rows, cik, cache, retained, cutoff):
        acc = row["accessionNumber"]
        raw = cache.get(filing_url(row | {"primaryDocument": acc + ".txt"}))
        try:
            ownership_subject(raw, cik=cik, accession=acc)
            own = ownership_identity(raw, row, cik=cik, cutoff=cutoff)
        except IdentityEvidenceError as error:
            reason = str(error)
            if reason != "ownership_subject_issuer_mismatch":
                text = " ".join(_soup(raw).get_text(" ", strip=True).split())
                if (
                    reason != "missing_or_ambiguous_cusip"
                    or re.search(r"\bCUSIP\b|(?<![A-Z0-9])[A-Z][0-9A-Z]{7}[0-9]\b", text, re.I)
                    or "ceased to be the beneficial owner of more than 5 percent" not in text
                ):
                    raise
            refusals.append({"accession": acc, "document_sha256": digest(raw), "reason": reason})
            continue
        if (
            own["cusip"] != evidence["cusip"]
            or class_signature(own["security_title"]) != ("common", "", "")
            or re.search(r"[$€£]|\b(?:par|nominal)\b", own["security_title"], re.I)
            or instant(own["accepted_at"]) < instant(evidence["available_by"])
        ):
            raise IdentityEvidenceError("offering_identity_ownership_conflict")
        reports.append(own)
    if len(reports) < 2:
        raise IdentityEvidenceError("offering_identity_insufficient_ownership")
    own = max(reports, key=lambda r: (instant(r["available_by"]), r["accession"]))
    if instant(own["available_by"]) < cutoff - timedelta(days=365):
        raise IdentityEvidenceError("offering_identity_no_recent_ownership")
    anchor = join_identity(
        periodic,
        own
        | {
            "reported_security_title": own["security_title"],
            "security_title": periodic["security_title"],
        },
    )
    return anchor | {
        "identity_basis": "same_SEC_subject_issuer_ordinary_share_offering_and_agreeing_ownership",
        "issuer_offering_evidence": evidence,
        "ownership_refusals": refusals,
        "ownership_commitments": [
            {"accession": r["accession"], "document_sha256": r["document_sha256"]} for r in reports
        ],
    }


def recompute(
    directory: Path, baseline_name: str, plan_name: str, *sources: Path
) -> dict[str, Any]:
    before = algorithms()
    match = re.fullmatch(r"issuer-vote-([a-f0-9]{64})\.json", baseline_name)
    if match is None or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,180}\.json", plan_name) is None:
        raise IdentityEvidenceError("invalid_offering_identity_input_names")
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        baseline = _read_private_file(fd, baseline_name, 32 * 1024**2)
        plan_raw = _read_private_file(fd, plan_name, 1024**2)
    if digest(baseline) != match[1]:
        raise IdentityEvidenceError("offering_identity_baseline_digest_mismatch")
    base = decode_json(baseline)
    if not isinstance(base, dict) or base.get("kind") != previous.KIND:
        raise IdentityEvidenceError("offering_identity_baseline_kind_mismatch")
    replay = previous.recompute(
        directory, base["baseline_filename"], base["reference_plan_filename"], *sources[:-1]
    )
    if canonical_bytes(replay) != canonical_bytes(base):
        raise IdentityEvidenceError("offering_identity_parent_replay_mismatch")
    eligible = {
        r["snapshot_member_key"]
        for r in base["results"]
        if r["status"] == "ownership_identity_unresolved"
    }
    plan = reference(plan_raw, eligible)
    key = plan["snapshot_member_key"]
    member = next(r for r in base["binding"]["members"] if r["snapshot_member_key"] == key)
    with (
        _capture_directory(directory, root=ARTIFACT_ROOT) as fd,
        _capture_directory(sources[-1], root=ARTIFACT_ROOT) as offering_fd,
    ):
        cache = SourceCache(fd, None, 1)
        offerings = SourceCache(offering_fd, None, 1)
        anchor = candidate(
            member,
            plan,
            cache,
            offerings,
            RetainedCheckpoint(sources[SOURCE_ARGS.index("retained-checkpoint")]),
            instant(base["binding"]["cutoff"]),
        )
        rows = append_results(base, {key: anchor})
    if algorithms() != before:
        raise IdentityEvidenceError("offering_identity_algorithm_changed")
    return {
        "schema_version": 1,
        "kind": KIND,
        "baseline_filename": baseline_name,
        "baseline_sha256": digest(baseline),
        "reference_plan_filename": plan_name,
        "reference_plan_sha256": digest(plan_raw),
        "binding": base["binding"],
        "algorithms": before,
        "source_receipts": {"identity": cache.used, "issuer_offering": offerings.used},
        "results": rows,
        "report": {
            **base["report"],
            "baseline_anchors": sum("anchor" in row for row in base["results"]),
            "anchors_added": 1,
            "statuses": dict(Counter(row["status"] for row in rows)),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("directory", *SOURCE_ARGS):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--references", required=True)
    args = parser.parse_args()
    try:
        result = recompute(
            args.directory,
            args.baseline,
            args.references,
            *(getattr(args, name.replace("-", "_")) for name in SOURCE_ARGS),
        )
        raw = canonical_bytes(result) + b"\n"
        filename = PREFIX + "-" + digest(raw) + ".json"
        with _capture_directory(args.directory, root=ARTIFACT_ROOT) as fd:
            _write_new(fd, filename, raw)
        print(json.dumps({"result_filename": filename, **result["report"]}, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        print(json.dumps({"refused": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
