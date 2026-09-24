from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import orjson
import pytest

from edgar_moe.copilot.diagnostics import DIAGNOSTIC_DISCLAIMER
from edgar_moe.forward.diagnostic_history import (
    MAX_HISTORY_REPORTS,
    DiagnosticHistoryError,
    build_forward_diagnostic_history,
    load_forward_diagnostic_reports,
    verify_forward_diagnostic_history,
)


def _raw_report(as_of: str, *, status: str = "ready") -> dict[str, object]:
    unique = {
        "diagnostic": True,
        "status": status,
        "official_horizon_sessions": 20,
        "horizon_sessions": 5,
        "as_of": as_of,
        "event_count": 8,
        "repeated_forecast_count": 1,
        "forecast_count": 8,
        "matched_count": 8,
        "matured_count": 8,
        "pending_count": 0,
        "unmatched_count": 0,
        "coverage": 1.0,
        "rank_ic": 0.1,
        "rmse": 0.05,
        "mae": 0.03,
        "directional_accuracy": 0.5,
        "next_maturity_at": None,
        "latest_maturity_at": None,
        "disclaimer": DIAGNOSTIC_DISCLAIMER,
        "selected_forecast_ids": ["must-not-leak"],
        "observations": [{"event_id": "must-not-leak"}],
    }
    return {
        "diagnostic": True,
        "official_horizon_sessions": 20,
        "horizon_sessions": 5,
        "as_of": as_of,
        "status": status,
        "forecast_count": 8,
        "matched_count": 8,
        "matured_count": 8,
        "pending_count": 0,
        "unmatched_count": 0,
        "coverage": 1.0,
        "rank_ic": 0.1,
        "rmse": 0.05,
        "mae": 0.03,
        "directional_accuracy": 0.5,
        "next_maturity_at": None,
        "latest_maturity_at": None,
        "observations": [{"event_id": "must-not-leak"}],
        "unmatched_forecasts": [{"forecast_id": "must-not-leak"}],
        "unique_event_evaluation": unique,
        "disclaimer": DIAGNOSTIC_DISCLAIMER,
    }


def _summaries(tmp_path: Path) -> tuple[dict[str, object], ...]:
    paths = [tmp_path / f"diagnostic-history-{index}.json" for index in range(1, 4)]
    reports = [
        _raw_report("2026-09-17T12:00:00+00:00"),
        _raw_report("2026-09-18T12:00:00+00:00"),
        _raw_report("2026-09-19T12:00:00+00:00"),
    ]
    summaries: list[dict[str, object]] = []
    for path, report in zip(paths, reports, strict=True):
        path.write_text(json.dumps(report), encoding="utf-8")
        summaries.extend(load_forward_diagnostic_reports([path]))
    return tuple(summaries)


def test_history_is_redacted_chronological_and_deterministic(tmp_path: Path) -> None:
    summaries = _summaries(tmp_path)

    history = build_forward_diagnostic_history(tuple(reversed(summaries)))
    repeated = build_forward_diagnostic_history(summaries)

    assert history["history_version"] == 2
    assert history["status"] == "ready"
    assert history["report_count"] == 3
    assert history["snapshot_independence"] == "not_assessed"
    assert history["promotion_eligible"] is False
    assert "Snapshots may overlap" in history["disclaimer"]
    assert "ready status means only" in history["disclaimer"]
    assert history["history_sha256"] == repeated["history_sha256"]
    assert [item["sequence"] for item in history["observations"]] == [1, 2, 3]
    serialized = json.dumps(history)
    assert "must-not-leak" not in serialized
    assert "observations" not in history["observations"][0]["unique_event_evaluation"]
    verify_forward_diagnostic_history(history)


def test_legacy_v1_history_remains_verifiable(tmp_path: Path) -> None:
    history = build_forward_diagnostic_history(_summaries(tmp_path))
    legacy = copy.deepcopy(history)
    legacy["history_version"] = 1
    legacy.pop("snapshot_independence")
    legacy.pop("promotion_eligible")
    legacy["disclaimer"] = (
        "Forward diagnostic history is research-only short-horizon evidence; it does not "
        "replace, modify, or contribute to the official 20-session forward evaluation."
    )
    legacy.pop("history_sha256")
    legacy["history_sha256"] = hashlib.sha256(
        orjson.dumps(legacy, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()

    verify_forward_diagnostic_history(legacy)


def test_insufficient_history_is_valid_and_verifies(tmp_path: Path) -> None:
    history = build_forward_diagnostic_history(_summaries(tmp_path)[:1], minimum_reports=3)

    assert history["status"] == "insufficient_history"
    assert history["review_required"] is True
    verify_forward_diagnostic_history(history)


def test_non_ready_report_requires_review_after_minimum_history(tmp_path: Path) -> None:
    summaries = list(_summaries(tmp_path))
    summaries[-1] = {**summaries[-1], "status": "awaiting_maturity"}

    history = build_forward_diagnostic_history(summaries)

    assert history["status"] == "review_required"


def test_raw_reports_cannot_bypass_the_redaction_boundary() -> None:
    raw = _raw_report("2026-09-17T12:00:00+00:00")

    with pytest.raises(DiagnosticHistoryError, match="unsafe fields"):
        build_forward_diagnostic_history([raw])


@pytest.mark.parametrize(
    "mutation",
    (
        lambda history: history["observations"][0].__setitem__("source_sha256", "bad"),
        lambda history: history["observations"][0].__setitem__("event_id", "secret"),
        lambda history: history.__setitem__("status", "review_required"),
        lambda history: history.__setitem__("history_sha256", "f" * 64),
        lambda history: history.__setitem__("snapshot_independence", "independent"),
        lambda history: history.__setitem__("promotion_eligible", True),
    ),
)
def test_tampered_history_is_rejected(tmp_path: Path, mutation: object) -> None:
    history = build_forward_diagnostic_history(_summaries(tmp_path))
    mutated = copy.deepcopy(history)
    mutation(mutated)  # type: ignore[operator]

    with pytest.raises(DiagnosticHistoryError):
        verify_forward_diagnostic_history(mutated)


def test_duplicate_timestamps_and_mixed_horizons_are_rejected(tmp_path: Path) -> None:
    summaries = list(_summaries(tmp_path))
    duplicate_timestamp = {**summaries[0], "source_sha256": "f" * 64}
    with pytest.raises(DiagnosticHistoryError, match="duplicate observation timestamps"):
        build_forward_diagnostic_history([summaries[0], duplicate_timestamp])

    mixed = copy.deepcopy(summaries[1])
    mixed["horizon_sessions"] = 6
    mixed["unique_event_evaluation"]["horizon_sessions"] = 6  # type: ignore[index]
    with pytest.raises(DiagnosticHistoryError, match="mixed horizons"):
        build_forward_diagnostic_history([summaries[0], mixed])


def test_verifier_does_not_reopen_deleted_source_files(tmp_path: Path) -> None:
    paths = [tmp_path / f"diagnostic-{index}.json" for index in range(1, 4)]
    for index, path in enumerate(paths, start=1):
        path.write_text(
            json.dumps(_raw_report(f"2026-09-{16 + index:02d}T12:00:00+00:00")),
            encoding="utf-8",
        )
    history = build_forward_diagnostic_history(load_forward_diagnostic_reports(paths))
    for path in paths:
        path.unlink()

    verify_forward_diagnostic_history(history)


def test_history_input_bounds_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(DiagnosticHistoryError, match="at least one"):
        load_forward_diagnostic_reports(())
    with pytest.raises(DiagnosticHistoryError, match="at most"):
        load_forward_diagnostic_reports(
            tuple(tmp_path / f"diagnostic-{index}.json" for index in range(MAX_HISTORY_REPORTS + 1))
        )
    with pytest.raises(DiagnosticHistoryError, match="at least one"):
        build_forward_diagnostic_history([])
    with pytest.raises(DiagnosticHistoryError, match="at most"):
        build_forward_diagnostic_history([{}] * (MAX_HISTORY_REPORTS + 1))
    with pytest.raises(DiagnosticHistoryError, match="cannot exceed"):
        build_forward_diagnostic_history(
            _summaries(tmp_path), minimum_reports=MAX_HISTORY_REPORTS + 1
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("history_version", 99),
        ("history_version", True),
        ("scope", "wrong"),
        ("research_only", False),
        ("v1_immutable", False),
        ("automatic_retraining", True),
        ("official_evaluation_untouched", False),
        ("status", "wrong"),
        ("review_required", "wrong"),
        ("minimum_reports", 0),
        ("report_count", MAX_HISTORY_REPORTS + 1),
        ("official_horizon_sessions", 19),
        ("horizon_sessions", 20),
        ("latest_as_of", "not-a-timestamp"),
        ("disclaimer", "wrong"),
        ("observations", []),
        ("history_sha256", "not-a-sha"),
    ),
)
def test_history_metadata_tampering_is_rejected(tmp_path: Path, field: str, value: object) -> None:
    history = build_forward_diagnostic_history(_summaries(tmp_path))
    mutated = copy.deepcopy(history)
    mutated[field] = value

    with pytest.raises(DiagnosticHistoryError):
        verify_forward_diagnostic_history(mutated)


def test_unavailable_unique_event_summary_remains_safe(tmp_path: Path) -> None:
    report = _raw_report("2026-09-17T12:00:00+00:00")
    report["unique_event_evaluation"] = {"status": "unavailable", "reason": "private detail"}
    path = tmp_path / "diagnostic-unavailable.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    history = build_forward_diagnostic_history(
        load_forward_diagnostic_reports([path]), minimum_reports=1
    )

    assert history["observations"][0]["unique_event_evaluation"] == {
        "status": "unavailable",
        "reason": "unique-event evaluation is unavailable",
    }
    verify_forward_diagnostic_history(history)
