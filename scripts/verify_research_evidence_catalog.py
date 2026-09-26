"""Verify the deployable research catalog using only Python's standard library.

This runs in CI and before a Vercel build, where research dependencies are not
installed. Runtime validation additionally uses strict typed API models.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

CATALOG = Path("src/edgar_moe/api/research_evidence_catalog.json")
LOCK = Path("src/edgar_moe/api/research_evidence_catalog.sha256")
SNAPSHOT = Path("data/demo/snapshot.json")
REPORT = Path("reports/locked_rank_ic_interval_2026-09-23.md")
COMPARATORS = {
    "Elastic Net",
    "Gradient-Boosted Tabular",
    "Fundamental-Only Expert",
    "Static Early-Fusion MLP",
    "Equal-Weight Expert Ensemble",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _object(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} schema is invalid")
    return value


def verify(
    catalog_path: Path = CATALOG,
    lock_path: Path = LOCK,
    snapshot_path: Path = SNAPSHOT,
    companion_report: Path = REPORT,
) -> None:
    expected = lock_path.read_text(encoding="ascii").strip()
    if len(expected) != 64 or expected != _sha256(catalog_path):
        raise ValueError("research evidence catalog hash mismatch")
    catalog = _object(
        json.loads(catalog_path.read_text(encoding="utf-8")),
        {"schema_version", "frozen_v1", "duration_aware_v2"},
        "research catalog",
    )
    if catalog["schema_version"] != 2:
        raise ValueError("unsupported research catalog schema")
    frozen = _object(
        catalog["frozen_v1"],
        {
            "dataset_id",
            "selection_hash",
            "locked_test_hash",
            "rank_ic_interval_95",
            "candidate_universe",
        },
        "frozen research evidence",
    )
    universe = _object(
        frozen["candidate_universe"],
        {"status", "screen_as_of", "first_validation_start", "locked_test_start"},
        "frozen candidate universe",
    )
    try:
        screen_as_of = date.fromisoformat(universe["screen_as_of"])
        first_validation = date.fromisoformat(universe["first_validation_start"])
        locked_start = date.fromisoformat(universe["locked_test_start"])
    except (TypeError, ValueError) as error:
        raise ValueError("frozen candidate-universe dates are invalid") from error
    if (
        universe["status"] != "retrospective_test_period_screen"
        or first_validation != date(2023, 1, 1)
        or locked_start != date(2025, 1, 1)
        or screen_as_of != date(2026, 7, 31)
    ):
        raise ValueError("frozen candidate-universe chronology is invalid")
    interval = _object(
        frozen["rank_ic_interval_95"],
        {"low", "high", "method", "calendar_months", "resamples", "source_sha256"},
        "frozen rank-IC interval",
    )
    if _sha256(companion_report) != interval["source_sha256"]:
        raise ValueError("reviewed v1 interval report hash mismatch")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    metadata = snapshot["metadata"]
    if (
        metadata["data_mode"] != "authenticated_locked_test"
        or metadata["as_of"] != universe["screen_as_of"]
        or metadata["selection_hash"] != frozen["selection_hash"]
        or metadata["locked_test_hash"] != frozen["locked_test_hash"]
        or not interval["low"] <= 0 <= interval["high"]
        or not interval["low"]
        <= snapshot["predictive_metrics"]["locked_test"]["rank_ic"]
        <= interval["high"]
    ):
        raise ValueError("catalog disagrees with the frozen study or interval")
    v2 = catalog["duration_aware_v2"]
    if not isinstance(v2, dict) or v2.get("status") not in {
        "pending_review",
        "reviewed_pretest",
    }:
        raise ValueError("v2 public state is invalid")
    if v2["status"] == "pending_review":
        _object(v2, {"status", "reason"}, "pending v2 evidence")
    else:
        _object(
            v2,
            {
                "status",
                "dataset_id",
                "source_manifest_sha256",
                "selection_sha256",
                "review_sha256",
                "candidate_universe_status",
                "oof_events",
                "champion_name",
                "champion_weighted_rank_ic",
                "uncertainty_method",
                "block_months",
                "bootstrap_resamples",
                "comparisons",
                "portfolio_status",
                "cost_scenarios",
                "cost_definition",
                "approval_reference",
                "interpretation",
            },
            "reviewed v2 evidence",
        )
        if v2["candidate_universe_status"] != "historical_membership_unverified":
            raise ValueError("reviewed v2 candidate-universe status is invalid")
        if not isinstance(v2["comparisons"], list) or len(v2["comparisons"]) != len(COMPARATORS):
            raise ValueError("reviewed v2 comparator roster is invalid")
        baselines = set()
        for item in v2["comparisons"]:
            comparison = _object(
                item,
                {"baseline", "rank_ic_delta", "interval_status", "interval_low", "interval_high"},
                "reviewed v2 comparison",
            )
            baselines.add(comparison["baseline"])
        if baselines != COMPARATORS:
            raise ValueError("reviewed v2 comparator roster is invalid")
        if not isinstance(v2["cost_scenarios"], list):
            raise ValueError("reviewed v2 cost scenarios are invalid")
        roster = set()
        for item in v2["cost_scenarios"]:
            scenario = _object(
                item, {"model", "cost_bps", "sharpe", "annualized_return"}, "cost scenario"
            )
            roster.add((scenario["model"], scenario["cost_bps"]))
        if v2["portfolio_status"] == "development_only":
            if v2["cost_definition"] != (
                "10/25/50 bps per unit of one-sided turnover plus configured short borrow"
            ):
                raise ValueError("reviewed v2 cost definition is invalid")
            expected_roster = {
                (name, cost)
                for name in {v2["champion_name"], "Fundamental-Only Expert"}
                for cost in (10, 25, 50)
            }
            if roster != expected_roster or len(v2["cost_scenarios"]) != len(expected_roster):
                raise ValueError("reviewed v2 cost scenario roster is invalid")
        elif v2["cost_scenarios"] or v2["cost_definition"] is not None:
            raise ValueError("unavailable portfolio has cost scenarios")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--lock", type=Path, default=LOCK)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--companion-report", type=Path, default=REPORT)
    args = parser.parse_args()
    verify(args.catalog, args.lock, args.snapshot, args.companion_report)
    print("Research evidence catalog verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
