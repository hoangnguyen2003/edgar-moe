"""Replay an issuer prospectus and dated SEC fund filing for a listed share class."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
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
from edgar_moe.data.historical_listed_isin import listing, nport_identity
from edgar_moe.data.screen_inputs import canonical_bytes, decode_json
from scripts import enrich_historical_fdic_equity as previous
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

KIND = "private_SEC_issuer_listed_ISIN_NPORT_supplement"
PREFIX = "listed-isin"
PLAN_SCOPE = "private_SEC_issuer_listed_ISIN_NPORT_only"
SOURCE_ARGS = (*previous.SOURCE_ARGS, "listed-isin-sources")
ACCESSION = r"[0-9]{10}-[0-9]{2}-[0-9]{6}"


def algorithms() -> dict[str, str]:
    algorithm = Path(__file__).resolve().parents[1] / "src/edgar_moe/data/historical_listed_isin.py"
    return {
        **previous.algorithms(),
        "listed_isin_algorithm_sha256": digest(algorithm.read_bytes()),
        "listed_isin_supplement_sha256": digest(Path(__file__).read_bytes()),
    }


def reference(raw: bytes, eligible: set[str]) -> dict[str, Any]:
    plan = decode_json(raw)
    if (
        not isinstance(plan, dict)
        or set(plan) != {"schema_version", "scope", "snapshot_member_key", "listing", "fund"}
        or type(plan["schema_version"]) is not int
        or plan["schema_version"] != 1
        or plan["scope"] != PLAN_SCOPE
        or not isinstance(plan["snapshot_member_key"], str)
        or plan["snapshot_member_key"] not in eligible
    ):
        raise IdentityEvidenceError("invalid_listed_isin_plan")
    for key, fields in (
        ("listing", {"accession", "primary", "exhibit"}),
        ("fund", {"accession", "filer_cik"}),
    ):
        ref = plan[key]
        if (
            not isinstance(ref, dict)
            or set(ref) != fields
            or not all(isinstance(val, str) for val in ref.values())
            or re.fullmatch(ACCESSION, ref["accession"]) is None
        ):
            raise IdentityEvidenceError("invalid_listed_isin_source_ref")
    files = [plan["listing"][k] for k in ("primary", "exhibit")]
    if (
        len(set(files)) != 2
        or any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.html?", f) is None for f in files)
        or re.fullmatch(r"[0-9]{10}", plan["fund"]["filer_cik"]) is None
        or int(plan["fund"]["filer_cik"]) == 0
    ):
        raise IdentityEvidenceError("invalid_listed_isin_source_ref")
    return plan


def registration_name(raw: bytes, cik: str) -> str:
    soup = _soup(raw, MAX_RETAINED_DOCUMENT_BYTES)
    names = [
        f
        for f in soup.find_all("ix:nonnumeric")
        if str(f.get("name", "")).casefold() == "dei:entityregistrantname"
    ]
    context_ref = names[0].get("contextref") if len(names) == 1 else None
    context = (
        soup.find_all("xbrli:context", id=context_ref)
        if isinstance(context_ref, str) and context_ref
        else []
    )
    if len(context) != 1:
        raise IdentityEvidenceError("listed_isin_registrant_name_context")
    identifiers = context[0].find_all("xbrli:identifier")
    if (
        len(identifiers) != 1
        or identifiers[0].get("scheme") not in {"http://www.sec.gov/CIK", "https://www.sec.gov/CIK"}
        or identifiers[0].get_text(strip=True) != cik
    ):
        raise IdentityEvidenceError("listed_isin_registrant_name_issuer")
    return " ".join(names[0].stripped_strings)


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
        periodic_raw = retained.filing(row)
        if periodic_raw is None:
            periodic_raw = cache.get(filing_url(row))
        try:
            period = periodic_anchor(
                periodic_raw,
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
        raise IdentityEvidenceError("listed_isin_periodic_registration_missing")
    issuer = registration_name(periodic_raw, cik)

    def read(filer: str, accession: str, filename: str) -> bytes:
        return sources.get(
            filing_url(
                {
                    "cik": filer,
                    "accessionNumber": accession,
                    "primaryDocument": filename,
                }
            )
        )

    ref = plan["listing"]
    accession = ref["accession"]
    listed = listing(
        read(cik, accession, accession + "-index.htm"),
        read(cik, accession, ref["primary"]),
        read(cik, accession, ref["exhibit"]),
        ref,
        period,
        issuer_name=issuer,
        cutoff=cutoff,
    )
    fund = plan["fund"]
    if fund["filer_cik"] == cik:
        raise IdentityEvidenceError("listed_isin_fund_must_differ_from_issuer")
    fund_proof = nport_identity(
        read(fund["filer_cik"], fund["accession"], fund["accession"] + ".txt"),
        fund,
        listed,
        cutoff=cutoff,
    )
    reports = []
    refusals = []
    for row in ownership_candidates(rows, cik, cache, retained, cutoff):
        acc = row["accessionNumber"]
        raw = cache.get(filing_url(row | {"primaryDocument": acc + ".txt"}))
        ownership_subject(raw, cik=cik, accession=acc)
        try:
            own = ownership_identity(raw, row, cik=cik, cutoff=cutoff)
        except IdentityEvidenceError as error:
            # An authenticated exit notice with no printed CUSIP cannot supply
            # agreement and is preserved as a refusal. Ambiguous IDs still fail.
            body = " ".join(_soup(raw).get_text(" ", strip=True).split())
            if (
                str(error) != "missing_or_ambiguous_cusip"
                or row["form"] != "SC 13G/A"
                or re.search(r"\bCUSIP\b|(?<![A-Z0-9])[A-Z][0-9A-Z]{7}[0-9]\b", body, re.I)
                or "ceased to be the beneficial owner of more than 5 percent" not in body
            ):
                raise
            refusals.append(
                {"accession": acc, "document_sha256": digest(raw), "reason": str(error)}
            )
            continue
        if (
            own["cusip"] != fund_proof["cusip"]
            or class_signature(own["security_title"]) != ("common", "", "")
            or re.search(r"[$€£]|\b(?:par|nominal)\b", own["security_title"], re.I)
        ):
            raise IdentityEvidenceError("listed_isin_ownership_conflict")
        reports.append(own)
    if len(reports) < 2:
        raise IdentityEvidenceError("listed_isin_ownership_insufficient")
    own = max(reports, key=lambda x: (instant(x["available_by"]), x["accession"]))
    result = join_identity(
        period,
        own
        | {
            "reported_security_title": own["security_title"],
            "security_title": period["security_title"],
        },
    )
    return result | {
        "available_by": max(
            instant(result["available_by"]),
            instant(fund_proof["available_by"]),
            instant(listed["available_by"]),
        ).isoformat(),
        "identity_basis": "issuer_registered_class_with_listed_ISIN_and_agreeing_NPORT_and_SEC_ownership",
        "issuer_listing_evidence": listed,
        "nport_evidence": fund_proof,
        "ownership_commitments": [
            {"accession": r["accession"], "document_sha256": r["document_sha256"]} for r in reports
        ],
        "ownership_refusals": refusals,
    }


def recompute(
    directory: Path, baseline_name: str, plan_name: str, *sources: Path
) -> dict[str, Any]:
    before = algorithms()
    match = re.fullmatch(r"fdic-listed-equity-([a-f0-9]{64})\.json", baseline_name)
    if match is None or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,180}\.json", plan_name) is None:
        raise IdentityEvidenceError("invalid_listed_isin_input_names")
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        raw = _read_private_file(fd, baseline_name, 32 * 1024**2)
        plan_raw = _read_private_file(fd, plan_name, 1024**2)
    if digest(raw) != match[1]:
        raise IdentityEvidenceError("listed_isin_baseline_digest_mismatch")
    base = decode_json(raw)
    if not isinstance(base, dict) or base.get("kind") != previous.KIND:
        raise IdentityEvidenceError("listed_isin_baseline_kind_mismatch")
    parent = previous.recompute(
        directory, base["baseline_filename"], base["reference_plan_filename"], *sources[:-1]
    )
    if canonical_bytes(parent) != canonical_bytes(base):
        raise IdentityEvidenceError("listed_isin_parent_replay_mismatch")
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
        _capture_directory(sources[-1], root=ARTIFACT_ROOT) as corroboration_fd,
    ):
        cache = SourceCache(fd, None, 1)
        corroboration = SourceCache(corroboration_fd, None, 1)
        anchor = candidate(
            member,
            plan,
            cache,
            corroboration,
            RetainedCheckpoint(sources[-3]),
            instant(base["binding"]["cutoff"]),
        )
        rows = append_results(base, {key: anchor})
    if algorithms() != before:
        raise IdentityEvidenceError("listed_isin_algorithm_changed")
    return {
        "schema_version": 1,
        "kind": KIND,
        "baseline_filename": baseline_name,
        "baseline_sha256": digest(raw),
        "reference_plan_filename": plan_name,
        "reference_plan_sha256": digest(plan_raw),
        "binding": base["binding"],
        "algorithms": before,
        "source_receipts": {"identity": cache.used, "listing_and_fund": corroboration.used},
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
