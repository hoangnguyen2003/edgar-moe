"""Private, pre-test-only attribution audit for paired XBRL-policy rebuilds.

This compares inputs, not model performance. In particular, it never indexes
locked-period targets, even though ResearchDataset.load reads the target array.
The legacy baseline is a new reconstruction under the v2 runtime, not frozen v1.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

import numpy as np
import orjson
import pandas as pd

from edgar_moe.features.dataset import ResearchDataset, dataset_xbrl_fact_policy


def compare_feature_policies(
    baseline: ResearchDataset,
    candidate: ResearchDataset,
    *,
    test_start: date,
    baseline_manifest_sha256: str,
    candidate_manifest_sha256: str,
) -> dict[str, Any]:
    """Check whether pre-test inputs isolate the XBRL policy change.

    Event IDs are used only in memory for alignment and never enter the report.
    Any change outside fundamentals makes causal attribution to XBRL alone
    unsafe, but does not prohibit a separately described v2 study.
    """
    if dataset_xbrl_fact_policy(baseline) != "legacy_v1":
        raise ValueError("baseline must use the legacy_v1 XBRL policy")
    if dataset_xbrl_fact_policy(candidate) != "duration_aware_v2":
        raise ValueError("candidate must use the duration_aware_v2 XBRL policy")
    if baseline.dataset_id == candidate.dataset_id:
        raise ValueError("paired dataset identities must differ")
    for digest in (baseline_manifest_sha256, candidate_manifest_sha256):
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("dataset manifest SHA-256 is invalid")

    baseline_indices, baseline_ids = _pretest_rows(baseline, test_start)
    candidate_indices, candidate_ids = _pretest_rows(candidate, test_start)
    if not baseline_ids or not candidate_ids:
        raise ValueError("both datasets need pre-test events")
    shared_source = (
        baseline.source_manifest_hash == candidate.source_manifest_hash
        and baseline.as_of == candidate.as_of
    )
    same_events = set(baseline_ids) == set(candidate_ids)
    report: dict[str, Any] = {
        "schema_version": 1,
        "comparison_type": "pretest_xbrl_policy_attribution",
        "baseline_dataset_id": baseline.dataset_id,
        "candidate_dataset_id": candidate.dataset_id,
        "baseline_manifest_sha256": baseline_manifest_sha256,
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "baseline_source_manifest_hash": baseline.source_manifest_hash,
        "candidate_source_manifest_hash": candidate.source_manifest_hash,
        "test_start": test_start.isoformat(),
        "scope": "pretest_inputs_only",
        "locked_targets_compared": False,
        "baseline_pretest_events": len(baseline_ids),
        "candidate_pretest_events": len(candidate_ids),
        "same_source_checkpoint": shared_source,
        "same_pretest_event_set": same_events,
    }
    if not shared_source or not same_events:
        report.update(
            {
                "status": "not_comparable",
                "reason": "source_or_pretest_event_set_differs",
            }
        )
        return _with_hash(report)

    candidate_positions = {
        event_id: index for event_id, index in zip(candidate_ids, candidate_indices, strict=True)
    }
    aligned_candidate = np.asarray([candidate_positions[event_id] for event_id in baseline_ids])
    baseline_positions = np.asarray(baseline_indices)
    same_timing = _same_event_timing(
        baseline.events.iloc[baseline_positions], candidate.events.iloc[aligned_candidate]
    )
    same_targets = np.array_equal(
        baseline.target[baseline_positions],
        candidate.target[aligned_candidate],
        equal_nan=True,
    )
    comparisons: dict[str, dict[str, Any]] = {}
    for name in ("text", "fundamental", "market", "regime"):
        baseline_array = baseline.regime if name == "regime" else baseline.modalities[name]
        candidate_array = candidate.regime if name == "regime" else candidate.modalities[name]
        comparisons[name] = _matrix_delta(
            baseline_array[baseline_positions],
            candidate_array[aligned_candidate],
            same_names=baseline.feature_names[name] == candidate.feature_names[name],
        )
    non_fundamental_same = all(
        comparisons[name]["exactly_equal"] for name in ("text", "market", "regime")
    )
    fundamentals_changed = not comparisons["fundamental"]["exactly_equal"]
    report.update(
        {
            "same_pretest_event_timing": same_timing,
            "same_pretest_targets": same_targets,
            "modalities": comparisons,
            "status": (
                "isolated_policy_change"
                if same_timing and same_targets and non_fundamental_same and fundamentals_changed
                else "no_observed_fundamental_change"
                if same_timing and same_targets and non_fundamental_same
                else "confounded"
            ),
        }
    )
    return _with_hash(report)


def _pretest_rows(dataset: ResearchDataset, test_start: date) -> tuple[list[int], list[str]]:
    entries = pd.to_datetime(dataset.events["entry_date"], errors="raise").dt.date
    horizons = pd.to_datetime(dataset.events["horizon_at"], utc=True, errors="raise").dt.date
    if entries.isna().any() or horizons.isna().any():
        raise ValueError("entry and horizon dates must be complete")
    indices = [
        int(index)
        for index, (entry, horizon) in enumerate(zip(entries, horizons, strict=True))
        if entry < test_start and horizon < test_start
    ]
    ids = dataset.events.iloc[indices]["event_id"].astype(str).tolist()
    if len(ids) != len(set(ids)):
        raise ValueError("pre-test event IDs must be unique")
    return indices, ids


def _same_event_timing(baseline: pd.DataFrame, candidate: pd.DataFrame) -> bool:
    for column in ("accepted_at", "entry_at", "horizon_at"):
        first = pd.to_datetime(baseline[column], utc=True).to_numpy()
        second = pd.to_datetime(candidate[column], utc=True).to_numpy()
        if not np.array_equal(first, second):
            return False
    return True


def _matrix_delta(
    baseline: np.ndarray,
    candidate: np.ndarray,
    *,
    same_names: bool,
) -> dict[str, Any]:
    if baseline.shape != candidate.shape or not same_names:
        return {
            "same_feature_names": same_names,
            "same_shape": baseline.shape == candidate.shape,
            "exactly_equal": False,
            "changed_rows": None,
            "changed_cells": None,
            "maximum_absolute_delta": None,
        }
    equal = (baseline == candidate) | (np.isnan(baseline) & np.isnan(candidate))
    differences = ~equal
    finite_pairs = np.isfinite(baseline) & np.isfinite(candidate)
    maximum_delta = (
        float(
            np.max(
                np.abs(
                    baseline[finite_pairs].astype(np.float64)
                    - candidate[finite_pairs].astype(np.float64)
                )
            )
        )
        if np.any(finite_pairs)
        else None
    )
    return {
        "same_feature_names": True,
        "same_shape": True,
        "exactly_equal": bool(np.all(equal)),
        "changed_rows": int(np.count_nonzero(np.any(differences, axis=1))),
        "changed_cells": int(np.count_nonzero(differences)),
        "maximum_absolute_delta": maximum_delta,
    }


def _with_hash(payload: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()
    return {**payload, "report_hash": digest}


def verify_feature_policy_report(report: Mapping[str, Any]) -> None:
    """Reject altered reports and fields outside the aggregate-only contract."""
    base_fields = {
        "schema_version",
        "comparison_type",
        "baseline_dataset_id",
        "candidate_dataset_id",
        "baseline_manifest_sha256",
        "candidate_manifest_sha256",
        "baseline_source_manifest_hash",
        "candidate_source_manifest_hash",
        "test_start",
        "scope",
        "locked_targets_compared",
        "baseline_pretest_events",
        "candidate_pretest_events",
        "same_source_checkpoint",
        "same_pretest_event_set",
        "status",
        "report_hash",
    }
    status = report.get("status")
    if status == "not_comparable":
        expected = base_fields | {"reason"}
        if report.get("reason") != "source_or_pretest_event_set_differs":
            raise ValueError("policy-delta reason is invalid")
    elif status in {"isolated_policy_change", "no_observed_fundamental_change", "confounded"}:
        expected = base_fields | {
            "same_pretest_event_timing",
            "same_pretest_targets",
            "modalities",
        }
        modalities = report.get("modalities")
        if not isinstance(modalities, dict) or set(modalities) != {
            "text",
            "fundamental",
            "market",
            "regime",
        }:
            raise ValueError("policy-delta modality roster is invalid")
        modality_fields = {
            "same_feature_names",
            "same_shape",
            "exactly_equal",
            "changed_rows",
            "changed_cells",
            "maximum_absolute_delta",
        }
        if any(
            not isinstance(item, dict) or set(item) != modality_fields
            for item in modalities.values()
        ):
            raise ValueError("policy-delta modality fields are invalid")
        for item in modalities.values():
            if any(
                type(item[field]) is not bool
                for field in ("same_feature_names", "same_shape", "exactly_equal")
            ):
                raise ValueError("policy-delta modality flags are invalid")
            for field in ("changed_rows", "changed_cells"):
                value = item[field]
                if value is not None and (type(value) is not int or value < 0):
                    raise ValueError("policy-delta modality counts are invalid")
            delta = item["maximum_absolute_delta"]
            if delta is not None and (
                type(delta) not in (int, float) or not math.isfinite(delta) or delta < 0
            ):
                raise ValueError("policy-delta modality delta is invalid")
    else:
        raise ValueError("policy-delta status is invalid")
    if set(report) != expected:
        raise ValueError("policy-delta report fields are invalid")
    if (
        report.get("schema_version") != 1
        or report.get("comparison_type") != "pretest_xbrl_policy_attribution"
        or report.get("scope") != "pretest_inputs_only"
        or report.get("locked_targets_compared") is not False
    ):
        raise ValueError("policy-delta report identity is invalid")
    for field in ("baseline_dataset_id", "candidate_dataset_id"):
        identity = report.get(field)
        if (
            not isinstance(identity, str)
            or re.fullmatch(r"research-\d{4}-\d{2}-\d{2}-[0-9a-f]{12}", identity) is None
        ):
            raise ValueError(f"policy-delta {field} is invalid")
    try:
        date.fromisoformat(str(report["test_start"]))
    except ValueError as error:
        raise ValueError("policy-delta test boundary is invalid") from error
    for field in ("baseline_pretest_events", "candidate_pretest_events"):
        count = report.get(field)
        if type(count) is not int or count < 1:
            raise ValueError(f"policy-delta {field} is invalid")
    for field in ("same_source_checkpoint", "same_pretest_event_set"):
        if type(report.get(field)) is not bool:
            raise ValueError(f"policy-delta {field} is invalid")
    if status != "not_comparable" and any(
        type(report.get(field)) is not bool
        for field in ("same_pretest_event_timing", "same_pretest_targets")
    ):
        raise ValueError("policy-delta comparison flags are invalid")
    if status == "not_comparable":
        if report["same_source_checkpoint"] and report["same_pretest_event_set"]:
            raise ValueError("policy-delta status contradicts source and event identity")
    else:
        if not report["same_source_checkpoint"] or not report["same_pretest_event_set"]:
            raise ValueError("policy-delta comparison lacks shared inputs")
        unchanged = (
            report["same_pretest_event_timing"]
            and report["same_pretest_targets"]
            and all(
                report["modalities"][name]["exactly_equal"] for name in ("text", "market", "regime")
            )
        )
        inferred = (
            "isolated_policy_change"
            if unchanged and not report["modalities"]["fundamental"]["exactly_equal"]
            else "no_observed_fundamental_change"
            if unchanged
            else "confounded"
        )
        if status != inferred:
            raise ValueError("policy-delta status contradicts comparison flags")
    for field in (
        "baseline_manifest_sha256",
        "candidate_manifest_sha256",
        "baseline_source_manifest_hash",
        "candidate_source_manifest_hash",
        "report_hash",
    ):
        digest = report.get(field)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(f"policy-delta {field} is invalid")
    unsigned = {key: value for key, value in report.items() if key != "report_hash"}
    expected_hash = hashlib.sha256(orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)).hexdigest()
    if report["report_hash"] != expected_hash:
        raise ValueError("policy-delta report hash does not match content")
