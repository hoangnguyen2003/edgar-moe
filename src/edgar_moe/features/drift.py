"""Prospective research-drift measurements.

This module deliberately measures data and frozen-model output drift without
opening a new training run, reading outcomes, or changing the v1 artifact.  A
drift report is research observability, not a service-health or retraining
decision.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import orjson

from edgar_moe.features.dataset import ResearchDataset, dataset_xbrl_fact_policy

DRIFT_MODALITIES = ("text", "fundamental", "market", "regime")
_EPSILON = 1e-12


@dataclass(frozen=True)
class DriftThresholds:
    """Review thresholds for a feature or component distribution comparison."""

    missing_rate_delta: float = 0.10
    standardized_mean_shift: float = 1.0
    population_stability_index: float = 0.20

    def __post_init__(self) -> None:
        if any(
            value <= 0
            for value in (
                self.missing_rate_delta,
                self.standardized_mean_shift,
                self.population_stability_index,
            )
        ):
            raise ValueError("Drift thresholds must be positive")


def build_research_drift_report(
    baseline: ResearchDataset,
    prospective: ResearchDataset,
    *,
    baseline_components: Mapping[str, np.ndarray] | None = None,
    prospective_components: Mapping[str, np.ndarray] | None = None,
    thresholds: DriftThresholds | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare a frozen training dataset with a later prospective dataset.

    The comparison uses only feature arrays and optional model component
    outputs.  Targets, daily returns, labels, and registry state are never read.
    Component outputs are expected to have been produced by the hash-pinned
    frozen predictor, but the function remains independent of the predictor so
    it can be tested and used in offline review tooling.
    """
    resolved_thresholds = thresholds or DriftThresholds()
    if prospective.as_of < baseline.as_of:
        raise ValueError("Prospective dataset as_of must not precede the baseline dataset as_of")
    baseline_policy = dataset_xbrl_fact_policy(baseline)
    prospective_policy = dataset_xbrl_fact_policy(prospective)
    if baseline_policy != prospective_policy:
        # Different fact policies define different fundamental features, so
        # their distributions are not comparable.
        raise ValueError(
            "Research drift requires one XBRL fact policy; "
            f"baseline {baseline_policy!r} differs from prospective {prospective_policy!r}"
        )

    feature_reports: dict[str, Any] = {}
    dimension_mismatches: list[str] = []
    for modality in DRIFT_MODALITIES:
        baseline_values = _matrix(_dataset_values(baseline, modality))
        prospective_values = _matrix(_dataset_values(prospective, modality))
        baseline_names = _feature_names(baseline, modality, baseline_values.shape[1])
        prospective_names = _feature_names(prospective, modality, prospective_values.shape[1])
        comparison = _compare_matrix(
            baseline_values,
            prospective_values,
            baseline_names=baseline_names,
            prospective_names=prospective_names,
            thresholds=resolved_thresholds,
        )
        comparison["baseline_rows"] = int(baseline_values.shape[0])
        comparison["prospective_rows"] = int(prospective_values.shape[0])
        feature_reports[modality] = comparison
        if not comparison["dimension_match"]:
            dimension_mismatches.append(modality)

    component_report = _compare_component_outputs(
        baseline_components,
        prospective_components,
        thresholds=resolved_thresholds,
    )
    feature_status = _section_status(feature_reports.values())
    component_status = str(component_report["status"])
    if dimension_mismatches:
        overall_status = "error"
    elif "warning" in (feature_status, component_status):
        overall_status = "warning"
    elif component_status == "not_supplied":
        overall_status = "incomplete"
    elif "error" in (feature_status, component_status):
        overall_status = "error"
    else:
        overall_status = "stable"

    payload: dict[str, Any] = {
        "report_version": 1,
        "scope": "research_drift_only",
        "v1_immutable": True,
        "automatic_retraining": False,
        "service_health_untouched": True,
        "status": overall_status,
        "baseline": _dataset_identity(baseline),
        "prospective": _dataset_identity(prospective),
        "context": dict(context) if context is not None else None,
        "temporal_order": "later_or_same_as_of",
        "thresholds": {
            "missing_rate_delta": resolved_thresholds.missing_rate_delta,
            "standardized_mean_shift": resolved_thresholds.standardized_mean_shift,
            "population_stability_index": resolved_thresholds.population_stability_index,
        },
        "features": feature_reports,
        "component_outputs": component_report,
        "summary": _summary(feature_reports, component_report, dimension_mismatches),
    }
    payload["report_hash"] = hashlib.sha256(
        orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    return payload


def verify_research_drift_report(report: Mapping[str, Any]) -> None:
    """Raise when a serialized drift report's content hash does not match."""
    expected = str(report.get("report_hash", ""))
    if len(expected) != 64:
        raise ValueError("Research drift report is missing a SHA-256 report_hash")
    unsigned = dict(report)
    unsigned.pop("report_hash", None)
    observed = hashlib.sha256(orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)).hexdigest()
    if observed != expected:
        raise ValueError("Research drift report content hash mismatch")


def _dataset_values(dataset: ResearchDataset, modality: str) -> Any:
    """Return one feature matrix without touching labels or outcomes."""
    if modality == "regime":
        return dataset.regime
    if modality in {"text", "fundamental", "market"}:
        return dataset.modalities[modality]
    raise ValueError(f"Unknown drift modality: {modality}")


def _matrix(values: Any) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2:
        raise ValueError(f"Drift arrays must be two-dimensional; observed shape {array.shape}")
    return array


def _feature_names(dataset: ResearchDataset, modality: str, dimension: int) -> list[str]:
    if modality == "regime":
        names = dataset.feature_names.get("regime", [])
    else:
        names = dataset.feature_names.get(modality, [])
    if len(names) != dimension:
        return [f"{modality}_{index}" for index in range(dimension)]
    return [str(name) for name in names]


def _dataset_identity(dataset: ResearchDataset) -> dict[str, Any]:
    return {
        "dataset_id": dataset.dataset_id,
        "as_of": dataset.as_of.isoformat(),
        "source_manifest_hash": dataset.source_manifest_hash,
        "event_count": int(len(dataset.events)),
        "included_events": int(dataset.attrition.get("included_events", len(dataset.events))),
    }


def _compare_matrix(
    baseline: np.ndarray,
    prospective: np.ndarray,
    *,
    baseline_names: list[str],
    prospective_names: list[str],
    thresholds: DriftThresholds,
) -> dict[str, Any]:
    dimension_match = baseline.shape[1] == prospective.shape[1]
    name_match = baseline_names == prospective_names
    rows: list[dict[str, Any]] = []
    for index in range(max(baseline.shape[1], prospective.shape[1])):
        name = baseline_names[index] if index < len(baseline_names) else prospective_names[index]
        if index >= baseline.shape[1] or index >= prospective.shape[1]:
            rows.append(
                {
                    "name": name,
                    "status": "dimension_mismatch",
                    "baseline_present": index < baseline.shape[1],
                    "prospective_present": index < prospective.shape[1],
                }
            )
            continue
        rows.append(
            _compare_vector(
                baseline[:, index],
                prospective[:, index],
                name=name,
                thresholds=thresholds,
            )
        )
    status = _section_status(rows)
    if not dimension_match or not name_match:
        status = "error"
    return {
        "status": status,
        "dimension_match": dimension_match,
        "feature_names_match": name_match,
        "baseline_dimension": int(baseline.shape[1]),
        "prospective_dimension": int(prospective.shape[1]),
        "features": rows,
    }


def _compare_vector(
    baseline: np.ndarray,
    prospective: np.ndarray,
    *,
    name: str,
    thresholds: DriftThresholds,
) -> dict[str, Any]:
    baseline_finite = baseline[np.isfinite(baseline)]
    prospective_finite = prospective[np.isfinite(prospective)]
    baseline_missing_rate = 1.0 - (len(baseline_finite) / max(len(baseline), 1))
    prospective_missing_rate = 1.0 - (len(prospective_finite) / max(len(prospective), 1))
    missing_rate_delta = prospective_missing_rate - baseline_missing_rate
    base_summary = _summary_vector(baseline_finite)
    prospective_summary = _summary_vector(prospective_finite)
    psi = _population_stability_index(baseline_finite, prospective_finite)
    baseline_std = base_summary["std"]
    mean_delta = _difference(prospective_summary["mean"], base_summary["mean"])
    standardized_shift = (
        None
        if baseline_std is None or baseline_std <= _EPSILON or mean_delta is None
        else mean_delta / baseline_std
    )

    reasons: list[str] = []
    if abs(missing_rate_delta) > thresholds.missing_rate_delta:
        reasons.append("missing_rate_delta")
    if (
        standardized_shift is not None
        and abs(standardized_shift) > thresholds.standardized_mean_shift
    ):
        reasons.append("standardized_mean_shift")
    elif (
        standardized_shift is None
        and baseline_std is not None
        and baseline_std <= _EPSILON
        and mean_delta is not None
        and abs(mean_delta) > _EPSILON
    ):
        reasons.append("constant_baseline_shift")
    if psi is not None and psi > thresholds.population_stability_index:
        reasons.append("population_stability_index")
    if not len(baseline_finite) or not len(prospective_finite):
        status = "warning" if reasons else "insufficient_data"
    else:
        status = "warning" if reasons else "stable"
    return {
        "name": name,
        "status": status,
        "reasons": reasons,
        "baseline": {
            **base_summary,
            "missing_rate": baseline_missing_rate,
            "finite_count": int(len(baseline_finite)),
            "row_count": int(len(baseline)),
        },
        "prospective": {
            **prospective_summary,
            "missing_rate": prospective_missing_rate,
            "finite_count": int(len(prospective_finite)),
            "row_count": int(len(prospective)),
        },
        "missing_rate_delta": missing_rate_delta,
        "mean_delta": mean_delta,
        "standardized_mean_shift": standardized_shift,
        "population_stability_index": psi,
    }


def _summary_vector(values: np.ndarray) -> dict[str, float | None]:
    if not len(values):
        return {"mean": None, "std": None, "q01": None, "q50": None, "q99": None}
    quantiles = np.quantile(values, [0.01, 0.50, 0.99])
    return {
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "q01": float(quantiles[0]),
        "q50": float(quantiles[1]),
        "q99": float(quantiles[2]),
    }


def _difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _population_stability_index(
    baseline: np.ndarray,
    prospective: np.ndarray,
    *,
    bins: int = 10,
) -> float | None:
    if len(baseline) < 2 or len(prospective) < 1:
        return None
    edges = np.unique(np.quantile(baseline, np.linspace(0.0, 1.0, bins + 1)))
    if len(edges) < 2:
        return 0.0 if np.isclose(np.mean(baseline), np.mean(prospective)) else None
    edges = np.concatenate(([-np.inf], edges[1:-1], [np.inf]))
    baseline_counts = np.histogram(baseline, bins=edges)[0].astype(np.float64)
    prospective_counts = np.histogram(prospective, bins=edges)[0].astype(np.float64)
    baseline_share = np.maximum(baseline_counts / len(baseline), _EPSILON)
    prospective_share = np.maximum(prospective_counts / len(prospective), _EPSILON)
    return float(
        np.sum((prospective_share - baseline_share) * np.log(prospective_share / baseline_share))
    )


def _compare_component_outputs(
    baseline: Mapping[str, np.ndarray] | None,
    prospective: Mapping[str, np.ndarray] | None,
    *,
    thresholds: DriftThresholds,
) -> dict[str, Any]:
    if baseline is None and prospective is None:
        return {
            "status": "not_supplied",
            "source": "not_supplied",
            "components": [],
        }
    if baseline is None or prospective is None:
        return {
            "status": "error",
            "source": "incomplete",
            "components": [],
            "error": "Both baseline and prospective component outputs are required",
        }
    baseline_expanded = _expand_outputs(baseline)
    prospective_expanded = _expand_outputs(prospective)
    names = sorted(set(baseline_expanded) | set(prospective_expanded))
    rows: list[dict[str, Any]] = []
    for name in names:
        left = baseline_expanded.get(name)
        right = prospective_expanded.get(name)
        if left is None or right is None:
            rows.append(
                {
                    "name": name,
                    "status": "dimension_mismatch",
                    "baseline_present": left is not None,
                    "prospective_present": right is not None,
                }
            )
            continue
        rows.append(_compare_vector(left, right, name=name, thresholds=thresholds))
    return {
        "status": _section_status(rows),
        "source": "hash_pinned_frozen_predictor",
        "components": rows,
        "baseline_rows": _output_row_count(baseline_expanded),
        "prospective_rows": _output_row_count(prospective_expanded),
    }


def _expand_outputs(outputs: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    expanded: dict[str, np.ndarray] = {}
    for name, raw_values in outputs.items():
        values = np.asarray(raw_values, dtype=np.float64)
        if values.ndim == 1:
            expanded[str(name)] = values
        elif values.ndim == 2:
            for index in range(values.shape[1]):
                expanded[f"{name}[{index}]"] = values[:, index]
        else:
            raise ValueError(
                f"Component output {name!r} must be one- or two-dimensional; "
                f"observed shape {values.shape}"
            )
    return expanded


def _output_row_count(outputs: Mapping[str, np.ndarray]) -> int | None:
    counts = {len(values) for values in outputs.values()}
    if not counts:
        return 0
    if len(counts) != 1:
        raise ValueError("Component output arrays must have a common row count")
    return counts.pop()


def _section_status(rows: Any) -> str:
    statuses = {str(row.get("status")) for row in rows}
    if "error" in statuses or "dimension_mismatch" in statuses:
        return "error"
    if "warning" in statuses:
        return "warning"
    if statuses and statuses <= {"insufficient_data"}:
        return "insufficient_data"
    return "stable"


def _summary(
    features: Mapping[str, Mapping[str, Any]],
    components: Mapping[str, Any],
    dimension_mismatches: list[str],
) -> dict[str, Any]:
    feature_rows = [
        feature for section in features.values() for feature in section.get("features", [])
    ]
    component_rows = list(components.get("components", []))
    warning_rows = [
        *[row for row in feature_rows if row.get("status") == "warning"],
        *[row for row in component_rows if row.get("status") == "warning"],
    ]
    return {
        "feature_count": len(feature_rows),
        "component_count": len(component_rows),
        "feature_warning_count": sum(row.get("status") == "warning" for row in feature_rows),
        "component_warning_count": sum(row.get("status") == "warning" for row in component_rows),
        "warning_names": [str(row.get("name")) for row in warning_rows],
        "dimension_mismatch_modalities": dimension_mismatches,
    }
