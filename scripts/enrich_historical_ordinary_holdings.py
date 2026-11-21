"""Verify historical ordinary-share identities from dated Form 13F and issuer filings."""

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
    valid_cusip,
)
from edgar_moe.data.historical_ordinary_holdings import filed_ordinary_holding, registrant_identity
from edgar_moe.data.screen_inputs import canonical_bytes, decode_json
from scripts import enrich_historical_offering_identity as previous
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
from scripts.enrich_historical_listed_isin import ACCESSION

KIND = "private_SEC_ordinary_share_holdings_supplement"
PREFIX = "ordinary-holdings"
PLAN_SCOPE = "private_SEC_ordinary_share_holdings_only"
SOURCE_ARGS = (*previous.SOURCE_ARGS, "ordinary-holdings-sources")


def algorithms() -> dict[str, str]:
    algorithm = (
        Path(__file__).resolve().parents[1] / "src/edgar_moe/data/historical_ordinary_holdings.py"
    )
    return {
        **previous.algorithms(),
        "ordinary_holdings_algorithm_sha256": digest(algorithm.read_bytes()),
        "ordinary_holdings_supplement_sha256": digest(Path(__file__).read_bytes()),
    }


def references(raw: bytes, eligible: set[str]) -> list[dict[str, Any]]:
    plan = decode_json(raw)
    if (
        not isinstance(plan, dict)
        or set(plan) != {"schema_version", "scope", "cases"}
        or type(plan["schema_version"]) is not int
        or plan["schema_version"] != 1
        or plan["scope"] != PLAN_SCOPE
        or not isinstance(plan["cases"], list)
        or not 1 <= len(plan["cases"]) <= len(eligible)
    ):
        raise IdentityEvidenceError("invalid_ordinary_holdings_plan")
    seen: set[str] = set()
    for case in plan["cases"]:
        if (
            not isinstance(case, dict)
            or set(case) != {"snapshot_member_key", "reports"}
            or not isinstance(case["snapshot_member_key"], str)
            or case["snapshot_member_key"] not in eligible - seen
            or not isinstance(case["reports"], list)
            or len(case["reports"]) != 2
        ):
            raise IdentityEvidenceError("invalid_ordinary_holdings_case")
        seen.add(case["snapshot_member_key"])
        accessions: set[str] = set()
        for report in case["reports"]:
            if (
                not isinstance(report, dict)
                or set(report) != {"manager_cik", "accession"}
                or not isinstance(report["manager_cik"], str)
                or re.fullmatch(r"[0-9]{10}", report["manager_cik"]) is None
                or not isinstance(report["accession"], str)
                or re.fullmatch(ACCESSION, report["accession"]) is None
                or report["accession"] in accessions
            ):
                raise IdentityEvidenceError("invalid_ordinary_holdings_reference")
            accessions.add(report["accession"])
    return list(plan["cases"])


def _reported_identifiers(payload: bytes) -> set[str]:
    document = " ".join(_soup(payload).get_text(" ", strip=True).upper().split())
    identifiers: set[str] = set()
    for label in re.finditer(r"CUSIP\s+(?:NUMBER|NO\.?)", document):
        region = document[max(0, label.start() - 64) : label.end() + 64]
        for value in re.findall(r"(?<![A-Z0-9])([A-Z0-9]{8}[0-9])(?![A-Z0-9])", region):
            if valid_cusip(value):
                identifiers.add(value)
    for field in re.findall(
        r"\(\s*TITLE OF CLASS OF SECURITIES\s*\)(.*?)\(\s*CUSIP (?:NUMBER|NO\.?)[^)]*\)",
        document,
    ):
        value = re.sub(r"[\s-]", "", field)
        if valid_cusip(value):
            identifiers.add(value)
    return identifiers


def _ownership_reports(
    rows: list[dict[str, Any]],
    cik: str,
    share_class: str,
    cache: SourceCache,
    retained: RetainedCheckpoint,
    cutoff: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    # Compare ownership reports in the same one-year window as the holdings.
    lower = cutoff - timedelta(days=365)
    reports = []
    refusals = []
    refused_identifiers: set[str] = set()
    for row in ownership_candidates(rows, cik, cache, retained, cutoff):
        if instant(row["acceptanceDateTime"]) < lower:
            continue
        accession = row["accessionNumber"]
        raw = cache.get(filing_url(row | {"primaryDocument": accession + ".txt"}))
        try:
            ownership_subject(raw, cik=cik, accession=accession)
            own = ownership_identity(raw, row, cik=cik, cutoff=cutoff)
        except IdentityEvidenceError as error:
            reason = str(error)
            if reason == "ambiguous_security_class":
                ids = _reported_identifiers(raw)
                if len(ids) != 1:
                    raise
                refused_identifiers.update(ids)
            elif reason == "missing_or_ambiguous_cusip":
                notice = " ".join(_soup(raw).get_text(" ", strip=True).lower().split())
                if (
                    "cusip" in notice
                    or "ceased to be the beneficial owner of more than 5 percent" not in notice
                ):
                    raise
            elif reason != "ownership_subject_issuer_mismatch":
                raise
        else:
            label = " ".join(own["security_title"].upper().split())
            if label == "COMMON STOCK AND CDI" and not share_class:
                reason = "mixed_ordinary_share_and_depositary_class"
                refused_identifiers.add(own["cusip"])
            elif re.fullmatch(r"(?:(?:CLASS|CL) [A-Z0-9]+ )?COMMON STOCK", label) and (
                class_signature(label) == ("common", share_class, "")
            ):
                reports.append(own)
                continue
            else:
                raise IdentityEvidenceError("ordinary_holdings_ownership_class_conflict")
        refusals.append({"accession": accession, "document_sha256": digest(raw), "reason": reason})
    if not reports:
        raise IdentityEvidenceError("ordinary_holdings_no_recent_ownership")
    if len({r["cusip"] for r in reports} | refused_identifiers) != 1:
        raise IdentityEvidenceError("ordinary_holdings_ownership_identifier_conflict")
    return reports, refusals


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
        raise IdentityEvidenceError("ordinary_holdings_periodic_registration_missing")
    family, share_class, rights = class_signature(periodic["security_title"])
    if family != "ordinary" or rights:
        raise IdentityEvidenceError("ordinary_holdings_wrong_periodic_class")
    issuer, country = registrant_identity(raw, cik)
    reports, refusals = _ownership_reports(rows, cik, share_class, cache, retained, cutoff)
    own = max(reports, key=lambda r: (instant(r["available_by"]), r["accession"]))
    holdings = []
    for report in plan["reports"]:
        accession = report["accession"]
        url = filing_url(
            {
                "cik": report["manager_cik"],
                "accessionNumber": accession,
                "primaryDocument": accession + ".txt",
            }
        )
        holdings.append(
            filed_ordinary_holding(
                sources.get(url),
                report,
                issuer_name=issuer,
                issuer_country=country,
                registered_title=periodic["security_title"],
                cusip=own["cusip"],
                cutoff=cutoff,
            )
        )
    if (holdings[0]["manager_cik"], holdings[0]["report_period"]) == (
        holdings[1]["manager_cik"],
        holdings[1]["report_period"],
    ):
        raise IdentityEvidenceError("ordinary_holdings_repeated_report_period")
    anchor = join_identity(
        periodic,
        own
        | {
            "reported_security_title": own["security_title"],
            "security_title": periodic["security_title"],
        },
    )
    return anchor | {
        "identity_basis": "same_SEC_registered_ordinary_share_and_ownership_with_dated_holdings",
        "available_by": max(
            instant(row["available_by"]) for row in [anchor, *holdings]
        ).isoformat(),
        "ordinary_holdings_evidence": holdings,
        "ownership_refusals": refusals,
        "ownership_commitments": [
            {"accession": r["accession"], "document_sha256": r["document_sha256"]} for r in reports
        ],
    }


def recompute(
    directory: Path, baseline_name: str, plan_name: str, *sources: Path
) -> dict[str, Any]:
    before = algorithms()
    match = re.fullmatch(r"issuer-offering-([a-f0-9]{64})\.json", baseline_name)
    if match is None or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,180}\.json", plan_name) is None:
        raise IdentityEvidenceError("invalid_ordinary_holdings_input_names")
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        baseline = _read_private_file(fd, baseline_name, 32 * 1024**2)
        plan_raw = _read_private_file(fd, plan_name, 1024**2)
    if digest(baseline) != match[1]:
        raise IdentityEvidenceError("ordinary_holdings_baseline_digest_mismatch")
    base = decode_json(baseline)
    if not isinstance(base, dict) or base.get("kind") != previous.KIND:
        raise IdentityEvidenceError("ordinary_holdings_baseline_kind_mismatch")
    replay = previous.recompute(
        directory, base["baseline_filename"], base["reference_plan_filename"], *sources[:-1]
    )
    if canonical_bytes(replay) != canonical_bytes(base):
        raise IdentityEvidenceError("ordinary_holdings_parent_replay_mismatch")
    eligible = {
        r["snapshot_member_key"]
        for r in base["results"]
        if r["status"] == "ownership_identity_unresolved"
    }
    cases = references(plan_raw, eligible)
    members = {row["snapshot_member_key"]: row for row in base["binding"]["members"]}
    with (
        _capture_directory(directory, root=ARTIFACT_ROOT) as fd,
        _capture_directory(sources[-1], root=ARTIFACT_ROOT) as holdings_fd,
    ):
        cache = SourceCache(fd, None, 1)
        holdings = SourceCache(holdings_fd, None, 1)
        retained = RetainedCheckpoint(sources[SOURCE_ARGS.index("retained-checkpoint")])
        cutoff = instant(base["binding"]["cutoff"])
        additions = {
            case["snapshot_member_key"]: candidate(
                members[case["snapshot_member_key"]], case, cache, holdings, retained, cutoff
            )
            for case in cases
        }
        rows = append_results(base, additions)
    if algorithms() != before:
        raise IdentityEvidenceError("ordinary_holdings_algorithm_changed")
    return {
        "schema_version": 1,
        "kind": KIND,
        "baseline_filename": baseline_name,
        "baseline_sha256": digest(baseline),
        "reference_plan_filename": plan_name,
        "reference_plan_sha256": digest(plan_raw),
        "binding": base["binding"],
        "algorithms": before,
        "source_receipts": {"identity": cache.used, "ordinary_holdings": holdings.used},
        "results": rows,
        "report": {
            **base["report"],
            "baseline_anchors": sum("anchor" in row for row in base["results"]),
            "anchors_added": len(additions),
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
