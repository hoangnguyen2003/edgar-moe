from __future__ import annotations

import copy

import pytest

from edgar_moe.forward.audit import audit_diagnostic, audit_official


def report(scores: list[float], labels: list[float]) -> dict:
    return {
        "diagnostic": True,
        "unique_event_evaluation": {
            "selection_rule": "earliest_forecast_per_model_and_event",
            "matured_count": len(scores),
            "observations": [
                {"model_id": "v1", "event_id": str(i), "score": s, "realized_abnormal_return": y}
                for i, (s, y) in enumerate(zip(scores, labels, strict=True))
            ],
        },
    }


def test_audit_baselines_and_no_mutation() -> None:
    source = report([-1, 1, 0], [-1, 1, 0])
    before = copy.deepcopy(source)
    result = audit_diagnostic(source)
    assert source == before
    assert result["model"]["rmse"] == 0
    assert result["zero_return_baseline"]["rmse"] == pytest.approx((2 / 3) ** 0.5)
    assert result["always_nonnegative_accuracy"] == 2 / 3
    assert result["leave_one_out_rank_ic_min"] == pytest.approx(1)


def test_empty_and_constant_samples() -> None:
    assert audit_diagnostic(report([], []))["model"]["rmse"] is None
    assert audit_diagnostic(report([1, 1], [0, 1]))["leave_one_out_rank_ic_min"] is None


def test_reject_invalid_evaluation() -> None:
    with pytest.raises(ValueError, match="earliest"):
        audit_diagnostic({"diagnostic": True})
    with pytest.raises(ValueError, match="diagnostic-only"):
        audit_diagnostic({})
    with pytest.raises(ValueError, match="Non-finite"):
        audit_diagnostic(report([float("nan")], [1]))
    source = report([1, 2], [1, 2])
    source["unique_event_evaluation"]["observations"][1]["event_id"] = "0"
    with pytest.raises(ValueError, match="Duplicate"):
        audit_diagnostic(source)
    source = report([1], [1])
    source["unique_event_evaluation"]["matured_count"] = 2
    with pytest.raises(ValueError, match="count"):
        audit_diagnostic(source)


def official_page() -> dict:
    return {
        "offset": 0,
        "total": 3,
        "items": [
            {
                "forecast_id": "early",
                "model_id": "v1",
                "event_id": "a",
                "forecast_as_of": "2026-08-01T00:00:00Z",
                "score": 0.1,
                "fundamental_score": 0.2,
                "realized_abnormal_return": None,
            },
            {
                "forecast_id": "late",
                "model_id": "v1",
                "event_id": "a",
                "forecast_as_of": "2026-08-02T00:00:00Z",
                "score": 0.1,
                "fundamental_score": 0.2,
                "realized_abnormal_return": 0.2,
            },
            {
                "forecast_id": "other",
                "model_id": "v1",
                "event_id": "b",
                "forecast_as_of": "2026-08-01T00:00:00Z",
                "score": 0.1,
                "fundamental_score": 0.2,
                "realized_abnormal_return": 0.2,
            },
        ],
    }


def test_official_selection_and_paired_comparison() -> None:
    source = official_page()
    before = copy.deepcopy(source)
    audit = audit_official(source)
    assert source == before
    assert audit["selected_forecast_ids"] == ["early", "other"]
    assert audit["model"]["matured_count"] == 1
    assert audit["model"]["coverage"] == 0.5
    assert audit["component_comparison"]["fundamental_anchor"]["rmse"] == 0
    source["items"][2]["fundamental_score"] = None
    audit = audit_official(source)
    assert audit["component_comparison"]["paired_count"] == 0
    assert audit["component_comparison"]["missing_component_count"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("total", 4),
        ("offset", 1),
    ],
)
def test_official_rejects_partial_export(field: str, value: int) -> None:
    source = official_page()
    source[field] = value
    with pytest.raises(ValueError, match="complete"):
        audit_official(source)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("forecast_id", "early", "Duplicate"),
        ("model_id", "v2", "one model"),
        ("forecast_as_of", "2026-08-01", "aware"),
        ("score", float("inf"), "Non-finite"),
        ("fundamental_score", float("nan"), "Non-finite"),
    ],
)
def test_official_rejects_invalid_rows(field: str, value: object, match: str) -> None:
    source = official_page()
    source["items"][2][field] = value
    with pytest.raises(ValueError, match=match):
        audit_official(source)


def test_official_empty_export() -> None:
    assert audit_official({"offset": 0, "total": 0, "items": []})["model"]["rmse"] is None
