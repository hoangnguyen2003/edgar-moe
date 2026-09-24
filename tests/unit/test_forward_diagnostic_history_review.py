from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import orjson
import pytest
from typer.testing import CliRunner

from edgar_moe import cli
from edgar_moe.copilot.diagnostics import DIAGNOSTIC_DISCLAIMER
from edgar_moe.forward.diagnostic_history import build_forward_diagnostic_history
from edgar_moe.forward.diagnostic_history_review import (
    DiagnosticHistoryReviewError,
    build_forward_diagnostic_history_review,
    read_forward_diagnostic_history_review,
    verify_forward_diagnostic_history_review,
    write_forward_diagnostic_history_review,
)

runner = CliRunner()


def _summary(as_of: str, source: str) -> dict[str, object]:
    unique = {
        "status": "ready",
        "official_horizon_sessions": 20,
        "horizon_sessions": 5,
        "as_of": as_of,
        "event_count": 2,
        "repeated_forecast_count": 0,
        "forecast_count": 2,
        "matched_count": 2,
        "matured_count": 2,
        "pending_count": 0,
        "coverage": 1.0,
        "rank_ic": 0.1,
        "rmse": 0.05,
        "mae": 0.03,
        "directional_accuracy": 0.5,
        "next_maturity_at": None,
        "latest_maturity_at": None,
    }
    return {
        "diagnostic": True,
        "official_horizon_sessions": 20,
        "horizon_sessions": 5,
        "as_of": as_of,
        "status": "ready",
        "forecast_count": 2,
        "matched_count": 2,
        "matured_count": 2,
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
        "unique_event_evaluation": unique,
        "source_sha256": source * 64,
    }


def _history(*, minimum_reports: int = 3) -> dict[str, object]:
    return build_forward_diagnostic_history(
        (
            _summary("2026-09-17T12:00:00+00:00", "a"),
            _summary("2026-09-18T12:00:00+00:00", "b"),
            _summary("2026-09-19T12:00:00+00:00", "c"),
        ),
        minimum_reports=minimum_reports,
    )


def _review(
    history: dict[str, object] | None = None,
    *,
    decision: str = "acknowledged",
    reason_codes: tuple[str, ...] = (),
    summary_reviewed: bool = True,
    limitations_acknowledged: bool = True,
) -> dict[str, object]:
    return build_forward_diagnostic_history_review(
        _history() if history is None else history,
        reviewer_id="hoangnguyen2003",
        decision=decision,
        summary_reviewed=summary_reviewed,
        limitations_acknowledged=limitations_acknowledged,
        reason_codes=reason_codes,
        reviewed_at=datetime(2026, 9, 24, 2, 0, tzinfo=UTC),
    )


def _rehash(review: dict[str, object]) -> None:
    review.pop("review_sha256")
    review["review_sha256"] = hashlib.sha256(
        orjson.dumps(review, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()


def test_review_is_separate_hash_pinned_and_explicitly_self_attested() -> None:
    history = _history()
    original_hash = history["history_sha256"]

    review = _review(history)

    assert history["history_sha256"] == original_hash
    assert history["human_review_status"] == "not_recorded"
    assert review["source_history"]["history_sha256"] == original_hash
    assert review["decision"] == "acknowledged"
    assert review["reason_codes"] == []
    assert all(review["acknowledgements"].values())
    assert "self-attested" in review["disclaimer"]
    assert "digital signature" in review["disclaimer"]
    assert "observations" not in review
    assert "rank_ic" not in review
    assert "forecast_id" not in orjson.dumps(review).decode()
    verify_forward_diagnostic_history_review(review, history=history)


def test_follow_up_requires_stable_reason_codes() -> None:
    review = _review(
        decision="follow_up_required",
        reason_codes=("metric_anomaly", "maturity_or_coverage_concern"),
    )

    assert review["decision"] == "follow_up_required"
    assert review["reason_codes"] == ["maturity_or_coverage_concern", "metric_anomaly"]
    verify_forward_diagnostic_history_review(review)

    with pytest.raises(DiagnosticHistoryReviewError, match="at least one reason"):
        _review(decision="follow_up_required")
    with pytest.raises(DiagnosticHistoryReviewError, match="cannot include follow-up"):
        _review(reason_codes=("metric_anomaly",))

    incomplete = _history(minimum_reports=4)
    with pytest.raises(DiagnosticHistoryReviewError, match="non-ready history"):
        _review(incomplete)
    pending = _review(
        incomplete,
        decision="follow_up_required",
        reason_codes=("insufficient_history",),
    )
    verify_forward_diagnostic_history_review(pending, history=incomplete)


@pytest.mark.parametrize(
    ("reviewer_id", "decision", "reason_codes", "message"),
    [
        ("someone@example.com", "acknowledged", (), "non-email"),
        ("unsafe/reviewer", "acknowledged", (), "non-email"),
        ("reviewer", "approve_model", (), "decision"),
        ("reviewer", "follow_up_required", ("free-form note",), "unsupported"),
    ],
)
def test_review_rejects_identity_injection_and_free_form_reasons(
    reviewer_id: str,
    decision: str,
    reason_codes: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(DiagnosticHistoryReviewError, match=message):
        build_forward_diagnostic_history_review(
            _history(),
            reviewer_id=reviewer_id,
            decision=decision,
            summary_reviewed=True,
            limitations_acknowledged=True,
            reason_codes=reason_codes,
            reviewed_at="2026-09-24T02:00:00Z",
        )


def test_review_rejects_naive_timestamp_and_unknown_data_fields() -> None:
    with pytest.raises(DiagnosticHistoryReviewError, match="timezone"):
        build_forward_diagnostic_history_review(
            _history(),
            reviewer_id="reviewer",
            decision="acknowledged",
            summary_reviewed=True,
            limitations_acknowledged=True,
            reviewed_at="2026-09-24T02:00:00",
        )

    review = _review()
    unsafe = copy.deepcopy(review)
    unsafe["notes"] = "raw private observations"
    _rehash(unsafe)
    with pytest.raises(DiagnosticHistoryReviewError, match="unknown fields"):
        verify_forward_diagnostic_history_review(unsafe)


def test_verifier_detects_mutation_and_wrong_source_history() -> None:
    review = _review()
    tampered = copy.deepcopy(review)
    tampered["reviewer_id"] = "another-reviewer"
    with pytest.raises(DiagnosticHistoryReviewError, match="content hash mismatch"):
        verify_forward_diagnostic_history_review(tampered)

    other_history = _history()
    other_history["observations"][0]["rank_ic"] = 0.2
    other_history.pop("history_sha256")
    other_history["history_sha256"] = hashlib.sha256(
        orjson.dumps(other_history, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    with pytest.raises(DiagnosticHistoryReviewError, match="does not match"):
        verify_forward_diagnostic_history_review(review, history=other_history)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("automatic_retraining", True, "cannot enable"),
        ("promotion_eligible", True, "cannot authorize"),
        ("v1_immutable", False, "preserve v1"),
    ],
)
def test_hash_recomputation_cannot_bypass_review_guardrails(
    field: str, value: object, message: str
) -> None:
    review = copy.deepcopy(_review())
    review[field] = value
    _rehash(review)

    with pytest.raises(DiagnosticHistoryReviewError, match=message):
        verify_forward_diagnostic_history_review(review)


def test_review_writer_is_private_readable_and_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "reviews" / "review.json"
    review = _review()

    write_forward_diagnostic_history_review(path, review)

    loaded = read_forward_diagnostic_history_review(path)
    assert loaded["review_sha256"] == review["review_sha256"]
    with pytest.raises(FileExistsError):
        write_forward_diagnostic_history_review(
            path,
            _review(decision="follow_up_required", reason_codes=("metric_anomaly",)),
        )


def test_cli_creates_verifies_and_refuses_to_replace_review(tmp_path: Path) -> None:
    history_path = tmp_path / "history.json"
    history_path.write_bytes(
        orjson.dumps(_history(), option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    )
    review_path = tmp_path / "review.json"
    arguments = [
        "forward-diagnostic-history-review",
        "--history",
        str(history_path),
        "--reviewer-id",
        "maintainer",
        "--decision",
        "acknowledged",
        "--output",
        str(review_path),
        "--confirm-reviewed",
        "--acknowledge-limitations",
    ]

    missing_review_confirmation = runner.invoke(
        cli.app, [argument for argument in arguments if argument != "--confirm-reviewed"]
    )
    assert missing_review_confirmation.exit_code == 2
    assert not review_path.exists()

    created = runner.invoke(cli.app, arguments)
    verified = runner.invoke(
        cli.app,
        [
            "forward-diagnostic-history-review-verify",
            str(review_path),
            "--history",
            str(history_path),
        ],
    )
    repeated = runner.invoke(cli.app, arguments)

    assert created.exit_code == 0, created.output
    assert "self-attested" in created.output
    assert verified.exit_code == 0, verified.output
    assert "source history matched" in verified.output
    assert repeated.exit_code == 2


@pytest.mark.parametrize(
    ("summary_reviewed", "limitations_acknowledged", "message"),
    [
        (False, True, "counts, maturity, coverage, and metrics"),
        (True, False, "research-only boundaries"),
    ],
)
def test_review_builder_requires_both_explicit_attestations(
    summary_reviewed: bool, limitations_acknowledged: bool, message: str
) -> None:
    with pytest.raises(DiagnosticHistoryReviewError, match=message):
        _review(
            summary_reviewed=summary_reviewed,
            limitations_acknowledged=limitations_acknowledged,
        )


@pytest.mark.parametrize("history_version", [1, 2, 3])
def test_review_accepts_all_supported_history_versions(history_version: int) -> None:
    history = _history()
    if history_version == 1:
        history["history_version"] = 1
        history.pop("diagnostic_review_required")
        history.pop("human_review_status")
        history.pop("snapshot_independence")
        history.pop("promotion_eligible")
        history["review_required"] = history["status"] != "ready"
        history["disclaimer"] = (
            "Forward diagnostic history is research-only short-horizon evidence; it does not "
            "replace, modify, or contribute to the official 20-session forward evaluation."
        )
    elif history_version == 2:
        history["history_version"] = 2
        history.pop("diagnostic_review_required")
        history.pop("human_review_status")
        history["review_required"] = history["status"] != "ready"
        history["disclaimer"] = (
            "Forward diagnostic history is a research-only sequence of short-horizon snapshots. "
            "Snapshots may overlap or reuse forecasts and labels; statistical independence is not "
            "assessed. A ready status means only that the configured number of valid snapshots was "
            "collected. This history is not promotion evidence, does not authorize retraining, and "
            "does not replace or contribute to the official 20-session forward evaluation."
        )
    history.pop("history_sha256")
    history["history_sha256"] = hashlib.sha256(
        orjson.dumps(history, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()

    review = _review(history)

    assert review["source_history"]["history_version"] == history_version
    verify_forward_diagnostic_history_review(review, history=history)
