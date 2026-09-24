"""The serving tier accepts only reviewed aggregates, never private research rows."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import orjson
import pytest

from edgar_moe.api.repository import SnapshotRepository
from edgar_moe.api.research_evidence import (
    CATALOG_PATH,
    LOCK_PATH,
    CatalogIntegrityError,
    build_research_evidence,
    load_public_catalog,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.publish_v2_research_evidence import stage_reviewed_catalog  # noqa: E402
from scripts.verify_research_evidence_catalog import verify  # noqa: E402


def _private_report() -> dict:
    report = {
        "schema_version": 1,
        "status": "pretest_development_only",
        "dataset_id": "duration-v2-test",
        "xbrl_fact_policy": "duration_aware_v2",
        "source_manifest_sha256": "1" * 64,
        "selection_sha256": "2" * 64,
        "locked_test_predictions": 0,
        "oof_events": 50,
        "champion": {"name": "Example", "weighted_rank_ic": 0.02},
        "comparisons": [
            {
                "baseline": name,
                "champion_minus_baseline_rank_ic": 0.01,
                "delta_interval_95": {"status": "ready", "low": -0.02, "high": 0.04},
            }
            for name in (
                "Elastic Net",
                "Gradient-Boosted Tabular",
                "Fundamental-Only Expert",
                "Static Early-Fusion MLP",
                "Equal-Weight Expert Ensemble",
            )
        ],
        "portfolio": {"status": "unavailable_return_calendar"},
        "uncertainty": {
            "method": "paired_calendar_month_moving_block_within_fold",
            "conditional_on_selection": True,
            "block_months": 2,
            "resamples": 1000,
        },
        "private_event_rows": [{"ticker": "SECRET"}],
    }
    report["review_sha256"] = hashlib.sha256(
        orjson.dumps(report, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    return report


def _write_private(path: Path, report: dict) -> None:
    path.write_bytes(orjson.dumps(report))


def test_pending_catalog_is_locked_to_frozen_snapshot() -> None:
    repo = SnapshotRepository(
        "data/demo/snapshot.json", lock_path="config/public_snapshot.lock.json"
    )
    result = build_research_evidence(repo)
    assert result.frozen_v1.locked_test_events > 0
    assert result.frozen_v1.locked_rank_ic_interval_95.low < 0
    assert result.frozen_v1.portfolio_10bps_sharpe < 0
    assert result.duration_aware_v2.status == "pending_review"
    assert "oof_events" not in result.duration_aware_v2.model_dump()
    verify()


def test_tampering_and_unknown_public_fields_fail_closed(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    lock = tmp_path / "catalog.sha256"
    catalog.write_bytes(CATALOG_PATH.read_bytes())
    lock.write_bytes(LOCK_PATH.read_bytes())
    catalog.write_bytes(catalog.read_bytes() + b" ")
    with pytest.raises(CatalogIntegrityError, match="hash mismatch"):
        load_public_catalog(catalog, lock)
    payload = orjson.loads(CATALOG_PATH.read_bytes())
    payload["duration_aware_v2"]["private_event_rows"] = [{"ticker": "SECRET"}]
    raw = orjson.dumps(payload)
    catalog.write_bytes(raw)
    lock.write_text(hashlib.sha256(raw).hexdigest() + "\n", encoding="ascii")
    with pytest.raises(CatalogIntegrityError, match="schema is invalid"):
        load_public_catalog(catalog, lock)


def test_reviewed_publication_allowlists_aggregates_and_refuses_overwrite(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    lock = tmp_path / "catalog.sha256"
    report_path = tmp_path / "private-review.json"
    catalog.write_bytes(CATALOG_PATH.read_bytes())
    lock.write_bytes(LOCK_PATH.read_bytes())
    _write_private(report_path, _private_report())
    staged = stage_reviewed_catalog(
        report_path, "review/issue-271", catalog_path=catalog, lock_path=lock
    )
    assert staged == hashlib.sha256(catalog.read_bytes()).hexdigest()
    public = catalog.read_text(encoding="utf-8")
    assert "SECRET" not in public
    assert "private_event_rows" not in public
    reviewed, _ = load_public_catalog(catalog, lock)
    assert reviewed.duration_aware_v2.status == "reviewed_pretest"
    verify(catalog, lock)
    with pytest.raises(ValueError, match="refusing overwrite"):
        stage_reviewed_catalog(
            report_path, "review/issue-271", catalog_path=catalog, lock_path=lock
        )


def test_reviewed_publication_preserves_cost_definition_and_complete_roster(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.json"
    lock = tmp_path / "catalog.sha256"
    report_path = tmp_path / "private-review.json"
    catalog.write_bytes(CATALOG_PATH.read_bytes())
    lock.write_bytes(LOCK_PATH.read_bytes())
    report = _private_report()
    report["portfolio"] = {
        "status": "development_only",
        "cost_definition": "10/25/50 bps per unit of one-sided turnover plus configured short borrow",
        "models": [
            {
                "model": name,
                "scenarios": [
                    {"cost_bps": cost, "sharpe": -0.2, "annualized_return": -0.03}
                    for cost in (10, 25, 50)
                ],
            }
            for name in ("Example", "Fundamental-Only Expert")
        ],
    }
    report.pop("review_sha256")
    report["review_sha256"] = hashlib.sha256(
        orjson.dumps(report, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    _write_private(report_path, report)
    stage_reviewed_catalog(report_path, "review/issue-271", catalog_path=catalog, lock_path=lock)
    reviewed, _ = load_public_catalog(catalog, lock)
    assert reviewed.duration_aware_v2.status == "reviewed_pretest"
    assert len(reviewed.duration_aware_v2.cost_scenarios) == 6
    assert "short borrow" in reviewed.duration_aware_v2.cost_definition
    verify(catalog, lock)


@pytest.mark.parametrize("mutation", ["hash", "locked", "uncertainty", "roster"])
def test_publication_rejects_untrusted_reports(tmp_path: Path, mutation: str) -> None:
    catalog = tmp_path / "catalog.json"
    lock = tmp_path / "catalog.sha256"
    report_path = tmp_path / "private-review.json"
    catalog.write_bytes(CATALOG_PATH.read_bytes())
    lock.write_bytes(LOCK_PATH.read_bytes())
    report = _private_report()
    if mutation == "hash":
        report["dataset_id"] = "changed"
    elif mutation == "locked":
        report["locked_test_predictions"] = 1
    elif mutation == "uncertainty":
        report["uncertainty"]["conditional_on_selection"] = False
    else:
        report["comparisons"].pop()
    if mutation != "hash":
        report.pop("review_sha256")
        report["review_sha256"] = hashlib.sha256(
            orjson.dumps(report, option=orjson.OPT_SORT_KEYS)
        ).hexdigest()
    _write_private(report_path, report)
    with pytest.raises(ValueError):
        stage_reviewed_catalog(
            report_path, "review/issue-271", catalog_path=catalog, lock_path=lock
        )
    assert catalog.read_bytes() == CATALOG_PATH.read_bytes()
    assert lock.read_bytes() == LOCK_PATH.read_bytes()
