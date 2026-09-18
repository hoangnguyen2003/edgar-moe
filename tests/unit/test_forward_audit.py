from __future__ import annotations

import copy

import pytest

from edgar_moe.forward.audit import audit_diagnostic


def report(scores: list[float], labels: list[float]) -> dict:
    return {"diagnostic": True, "unique_event_evaluation": {
        "selection_rule": "earliest_forecast_per_model_and_event",
        "matured_count": len(scores),
        "observations": [
            {"model_id": "v1", "event_id": str(i), "score": s,
             "realized_abnormal_return": y}
            for i, (s, y) in enumerate(zip(scores, labels, strict=True))
        ],
    }}


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
