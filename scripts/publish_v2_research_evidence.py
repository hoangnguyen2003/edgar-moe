"""Stage an explicitly reviewed, aggregate-only v2 catalog update for a PR.

The approval reference is a maintainer self-attestation, not authenticated
authorization. The resulting catalog and lock still require normal code review.
Never run this on an unreviewed private pretest report.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import orjson

from edgar_moe.api.models import V2ReviewedEvidence
from edgar_moe.api.research_evidence import (
    CATALOG_PATH,
    LOCK_PATH,
    PublicCatalog,
    load_public_catalog,
)

_APPROVAL = re.compile(r"^[A-Za-z0-9._/#-]{8,80}$")


def reviewed_aggregate(report: dict[str, Any], approval_reference: str) -> dict[str, Any]:
    """Project a private report onto a strict, non-row-level public allowlist."""
    if not _APPROVAL.fullmatch(approval_reference):
        raise ValueError("approval reference must be a short, non-secret identifier")
    recorded_hash = report.get("review_sha256")
    unsigned = {key: value for key, value in report.items() if key != "review_sha256"}
    observed_hash = hashlib.sha256(orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)).hexdigest()
    if recorded_hash != observed_hash:
        raise ValueError("private pretest review hash mismatch")
    if (
        report.get("schema_version") != 1
        or report.get("status") != "pretest_development_only"
        or report.get("xbrl_fact_policy") != "duration_aware_v2"
        or report.get("locked_test_predictions") != 0
    ):
        raise ValueError("report is not a duration-aware, pretest-only v2 review")
    champion = report["champion"]
    comparisons = []
    for item in report["comparisons"]:
        interval = item["delta_interval_95"]
        comparisons.append(
            {
                "baseline": item["baseline"],
                "rank_ic_delta": item["champion_minus_baseline_rank_ic"],
                "interval_status": interval["status"],
                "interval_low": interval.get("low"),
                "interval_high": interval.get("high"),
            }
        )
    portfolio = report["portfolio"]
    uncertainty = report["uncertainty"]
    if (
        uncertainty.get("method") != "paired_calendar_month_moving_block_within_fold"
        or uncertainty.get("conditional_on_selection") is not True
    ):
        raise ValueError("v2 uncertainty method or selection caveat is invalid")
    scenarios = []
    if portfolio["status"] == "development_only":
        if portfolio.get("cost_definition") != (
            "10/25/50 bps per unit of one-sided turnover plus configured short borrow"
        ):
            raise ValueError("private pretest cost definition is unexpected")
        for model in portfolio["models"]:
            for scenario in model["scenarios"]:
                scenarios.append(
                    {
                        "model": model["model"],
                        "cost_bps": scenario["cost_bps"],
                        "sharpe": scenario.get("sharpe"),
                        "annualized_return": scenario.get("annualized_return"),
                    }
                )
    public = {
        "status": "reviewed_pretest",
        "dataset_id": report["dataset_id"],
        "source_manifest_sha256": report["source_manifest_sha256"],
        "selection_sha256": report["selection_sha256"],
        "review_sha256": recorded_hash,
        "oof_events": report["oof_events"],
        "champion_name": champion["name"],
        "champion_weighted_rank_ic": champion["weighted_rank_ic"],
        "uncertainty_method": uncertainty["method"],
        "block_months": uncertainty["block_months"],
        "bootstrap_resamples": uncertainty["resamples"],
        "comparisons": comparisons,
        "portfolio_status": portfolio["status"],
        "cost_scenarios": scenarios,
        "cost_definition": (
            "10/25/50 bps per unit of one-sided turnover plus configured short borrow"
            if portfolio["status"] == "development_only"
            else None
        ),
        "approval_reference": approval_reference,
        "interpretation": (
            "Development-fold comparisons are conditional on model selection. "
            "No independent v2 ranking-skill or tradable-alpha claim is supported."
        ),
    }
    V2ReviewedEvidence.model_validate(public)
    return public


def stage_reviewed_catalog(
    report_path: Path,
    approval_reference: str,
    *,
    catalog_path: Path = CATALOG_PATH,
    lock_path: Path = LOCK_PATH,
) -> str:
    catalog, _old_hash = load_public_catalog(catalog_path, lock_path)
    if catalog.duration_aware_v2.status != "pending_review":
        raise ValueError("catalog already contains reviewed v2 evidence; refusing overwrite")
    report = orjson.loads(report_path.read_bytes())
    if not isinstance(report, dict):
        raise ValueError("private report must be a JSON object")
    public = reviewed_aggregate(report, approval_reference)
    payload = catalog.model_dump(mode="json")
    payload["duration_aware_v2"] = public
    PublicCatalog.model_validate(payload)
    raw = orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"
    digest = hashlib.sha256(raw).hexdigest()
    _replace_from_staged_bytes(catalog_path, raw)
    _replace_from_staged_bytes(lock_path, (digest + "\n").encode("ascii"))
    return digest


def _replace_from_staged_bytes(path: Path, contents: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-review", required=True, type=Path)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    args = parser.parse_args()
    digest = stage_reviewed_catalog(
        args.private_review,
        args.approval_reference,
        catalog_path=args.catalog,
        lock_path=args.lock,
    )
    print(f"Staged aggregate-only v2 catalog {digest}; review the resulting diff before publishing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
