from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from subprocess import run

import numpy as np
import pandas as pd
import pytest

from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.modeling.policy_delta import (
    compare_feature_policies,
    verify_feature_policy_report,
)

MANIFEST_KWARGS = {
    "baseline_manifest_sha256": "b" * 64,
    "candidate_manifest_sha256": "c" * 64,
}


def _dataset(*, policy: str, identity: str) -> ResearchDataset:
    events = pd.DataFrame(
        {
            "event_id": ["private-1", "private-2", "locked-private-3"],
            "entry_date": [date(2023, 6, 1), date(2024, 6, 1), date(2024, 12, 31)],
            "accepted_at": pd.to_datetime(
                ["2023-05-31T20:00:00Z", "2024-05-31T20:00:00Z", "2024-12-30T20:00:00Z"]
            ),
            "entry_at": pd.to_datetime(
                ["2023-06-01T13:30:00Z", "2024-06-01T13:30:00Z", "2024-12-31T13:30:00Z"]
            ),
            "horizon_at": pd.to_datetime(
                ["2023-07-01T20:00:00Z", "2024-07-01T20:00:00Z", "2025-02-01T20:00:00Z"]
            ),
        }
    )
    return ResearchDataset(
        dataset_id=f"research-2026-07-31-{'a' * 12 if identity == 'v1' else 'b' * 12}",
        as_of=date(2026, 7, 31),
        events=events,
        modalities={
            "text": np.array([[1.0], [2.0], [3.0]], dtype=np.float32),
            "fundamental": np.array([[0.1], [0.2], [0.3]], dtype=np.float32),
            "market": np.array([[0.4], [0.5], [0.6]], dtype=np.float32),
        },
        regime=np.array([[0.7], [0.8], [0.9]], dtype=np.float32),
        target=np.array([0.01, 0.02, 0.03], dtype=np.float32),
        daily_returns=pd.DataFrame(),
        availability=pd.DataFrame(),
        feature_names={
            "text": ["text_0"],
            "fundamental": ["return_on_assets"],
            "market": ["momentum_5d"],
            "regime": ["VIXCLS"],
        },
        attrition={},
        source_manifest_hash="a" * 64,
        provenance={"xbrl_fact_policy": policy},
    )


def _candidate(baseline: ResearchDataset) -> ResearchDataset:
    modalities = {name: values.copy() for name, values in baseline.modalities.items()}
    modalities["fundamental"][0, 0] = 0.3
    return replace(
        baseline,
        dataset_id=f"research-2026-07-31-{'b' * 12}",
        modalities=modalities,
        target=baseline.target.copy(),
        provenance={"xbrl_fact_policy": "duration_aware_v2"},
    )


def test_policy_delta_is_pretest_only_and_redacted() -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    candidate.modalities["text"][2, 0] = 999.0
    candidate.target[2] = 999.0

    report = compare_feature_policies(
        baseline, candidate, test_start=date(2025, 1, 1), **MANIFEST_KWARGS
    )

    assert report["status"] == "isolated_policy_change"
    assert report["baseline_pretest_events"] == 2
    assert report["locked_targets_compared"] is False
    assert report["modalities"]["fundamental"]["changed_rows"] == 1
    assert report["modalities"]["text"]["changed_rows"] == 0
    assert len(report["report_hash"]) == 64
    assert "private-1" not in str(report)
    assert "locked-private-3" not in str(report)
    assert "999" not in str(report)
    verify_feature_policy_report(report)

    altered = {**report, "baseline_pretest_events": 3}
    with pytest.raises(ValueError, match="hash"):
        verify_feature_policy_report(altered)
    leaked = {**report, "private_event_rows": [{"event_id": "private-1"}]}
    with pytest.raises(ValueError, match="fields"):
        verify_feature_policy_report(leaked)


def test_non_xbrl_pretest_change_is_confounded() -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    candidate.modalities["text"][1, 0] = 2.01

    report = compare_feature_policies(
        baseline, candidate, test_start=date(2025, 1, 1), **MANIFEST_KWARGS
    )

    assert report["status"] == "confounded"
    assert report["modalities"]["text"]["changed_cells"] == 1


def test_pretest_rows_align_by_identity_not_storage_order() -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    order = [1, 0, 2]
    candidate = replace(
        candidate,
        events=candidate.events.iloc[order].reset_index(drop=True),
        modalities={name: values[order] for name, values in candidate.modalities.items()},
        regime=candidate.regime[order],
        target=candidate.target[order],
    )

    report = compare_feature_policies(
        baseline, candidate, test_start=date(2025, 1, 1), **MANIFEST_KWARGS
    )

    assert report["status"] == "isolated_policy_change"
    assert report["same_pretest_event_set"] is True


def test_different_source_or_event_set_is_not_comparable() -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    candidate = replace(candidate, source_manifest_hash="b" * 64)

    report = compare_feature_policies(
        baseline, candidate, test_start=date(2025, 1, 1), **MANIFEST_KWARGS
    )

    assert report["status"] == "not_comparable"
    assert "modalities" not in report
    verify_feature_policy_report(report)


def test_wrong_policy_and_duplicate_pretest_ids_fail_closed() -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    with pytest.raises(ValueError, match="duration_aware_v2"):
        compare_feature_policies(
            baseline,
            replace(candidate, provenance={"xbrl_fact_policy": "legacy_v1"}),
            test_start=date(2025, 1, 1),
            **MANIFEST_KWARGS,
        )
    duplicate = baseline.events.copy()
    duplicate.loc[1, "event_id"] = duplicate.loc[0, "event_id"]
    with pytest.raises(ValueError, match="unique"):
        compare_feature_policies(
            replace(baseline, events=duplicate),
            candidate,
            test_start=date(2025, 1, 1),
            **MANIFEST_KWARGS,
        )


def test_policy_delta_cli_rejects_a_public_output_path(tmp_path: Path) -> None:
    completed = run(
        [
            sys.executable,
            "scripts/compare_xbrl_policy_inputs.py",
            "--baseline-dataset",
            "unused-v1",
            "--candidate-dataset",
            "unused-v2",
            "--output",
            str(tmp_path / "public" / "policy-delta.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "ignored data/artifacts/v2-reviews" in completed.stderr
