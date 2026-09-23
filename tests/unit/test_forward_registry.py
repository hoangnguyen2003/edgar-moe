from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from edgar_moe.forward.artifacts import LocalArtifactStore
from edgar_moe.forward.database import ImmutableRecordError, RegistryDatabase
from edgar_moe.forward.domain import (
    DatasetRegistration,
    ForecastDraft,
    LabelDraft,
    ModelRegistration,
    QualityCheckDraft,
    RunRegistration,
)
from edgar_moe.forward.metrics import forward_metrics, percentile_ranks, rank_ic_interval
from edgar_moe.forward.models import ForecastRecord
from edgar_moe.forward.registry import (
    ForwardRegistry,
    RegistryConflictError,
    RegistryStateError,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
AS_OF = datetime(2026, 8, 6, 21, 0, tzinfo=UTC)


def registry(tmp_path: Path) -> tuple[RegistryDatabase, ForwardRegistry]:
    database = RegistryDatabase(f"sqlite:///{tmp_path / 'registry.sqlite3'}")
    database.create_schema()
    return database, ForwardRegistry(database, actor="pytest")


def dataset_registration(dataset_id: str = "dataset-v2") -> DatasetRegistration:
    return DatasetRegistration(
        dataset_id=dataset_id,
        as_of=date(2026, 8, 6),
        source_manifest_hash=HASH_A,
        manifest_sha256=HASH_B,
        row_counts={"events": 10, "matured_labels": 8},
        provenance={"source": "fixture"},
    )


def model_registration() -> ModelRegistration:
    return ModelRegistration(
        model_id="edgar-moe-v1",
        name="EDGAR-MoE",
        version="1.0.0-frozen",
        training_dataset_id="dataset-v2",
        selection_hash=HASH_A,
        artifact_uri="local://sha256/model.pt",
        artifact_sha256=HASH_B,
        config_hash=HASH_C,
        champion_family="anchored_multimodal",
        parameters={"anchor_weight": 0.75},
        frozen_at=datetime(2026, 7, 31, tzinfo=UTC),
    )


def forecast(event_id: str, score: float = 0.2) -> ForecastDraft:
    return ForecastDraft(
        event_id=event_id,
        accession_number=f"0000000000-26-{event_id[-3:]:0>6}",
        security_id=f"asset-{event_id}",
        ticker=event_id.upper(),
        company_name=f"{event_id} Corp",
        form="10-Q",
        accepted_at=AS_OF - timedelta(hours=2),
        entry_at=AS_OF + timedelta(hours=16),
        entry_date=AS_OF.date() + timedelta(days=1),
        horizon_at=AS_OF + timedelta(days=30),
        industry_code="3571",
        score=score,
        rank=0.75,
        fundamental_score=score * 0.8,
        expert_weights={"text": 0.2, "fundamental": 0.6, "market": 0.2},
        expert_predictions={"text": 0.1, "fundamental": 0.2, "market": -0.1},
    )


def prepare_forecast_run(registry_service: ForwardRegistry) -> str:
    registry_service.register_dataset(dataset_registration())
    registry_service.register_model(model_registration())
    run = registry_service.start_run(
        RunRegistration(
            run_type="forecast",
            as_of=AS_OF,
            code_revision="deadbeef",
            config_hash=HASH_C,
            dataset_id="dataset-v2",
            model_id="edgar-moe-v1",
        ),
        run_id="run-forecast-1",
    )
    return run.run_id


def test_registry_is_idempotent_and_append_only(tmp_path: Path) -> None:
    database, registry_service = registry(tmp_path)
    assert not registry_service.has_dataset("dataset-v2")
    run_id = prepare_forecast_run(registry_service)
    assert registry_service.has_dataset("dataset-v2")

    first = registry_service.append_forecasts(run_id, [forecast("event-001")])
    second = registry_service.append_forecasts(run_id, [forecast("event-001")])
    assert first == {"inserted": 1, "idempotent_skips": 0}
    assert second == {"inserted": 0, "idempotent_skips": 1}

    with pytest.raises(RegistryConflictError, match="Conflicting forecast"):
        registry_service.append_forecasts(run_id, [forecast("event-001", score=0.3)])

    with database.session() as session:
        stored = session.scalar(select(ForecastRecord))
        assert stored is not None
        stored.score = 9.0
        with pytest.raises(ImmutableRecordError, match="append-only"):
            session.flush()
        session.rollback()

    database.dispose()


def test_registry_enforces_prospective_timing_and_state(tmp_path: Path) -> None:
    database, registry_service = registry(tmp_path)
    run_id = prepare_forecast_run(registry_service)
    already_mature = forecast("event-002").model_copy(
        update={"horizon_at": AS_OF - timedelta(seconds=1)}
    )
    with pytest.raises(RegistryStateError, match="already mature"):
        registry_service.append_forecasts(run_id, [already_mature])

    registry_service.complete_run(run_id, result_counts={"forecasts": 0})
    with pytest.raises(RegistryStateError, match="not active"):
        registry_service.append_forecasts(run_id, [forecast("event-003")])
    database.dispose()


def test_failed_run_messages_are_redacted_at_registry_boundary(tmp_path: Path) -> None:
    database, registry_service = registry(tmp_path)
    run_id = prepare_forecast_run(registry_service)

    registry_service.fail_run(
        run_id,
        error_message=(
            "provider rejected postgresql://reader:super-secret@example.test/registry "
            "password=another-secret"
        ),
    )

    failed_run = registry_service.list_runs(limit=1)[0]
    assert failed_run["error_message"] is not None
    assert "example.test" not in failed_run["error_message"]
    assert "super-secret" not in failed_run["error_message"]
    assert "another-secret" not in failed_run["error_message"]
    database.dispose()


def test_label_settlement_and_forward_performance(tmp_path: Path) -> None:
    database, registry_service = registry(tmp_path)
    forecast_run = prepare_forecast_run(registry_service)
    registry_service.append_forecasts(
        forecast_run,
        [forecast("event-001", 0.2), forecast("event-002", -0.1)],
    )
    registry_service.add_quality_checks(
        forecast_run,
        [
            QualityCheckDraft(
                name="feature_availability",
                status="passed",
                observed_value=1.0,
                threshold=1.0,
            )
        ],
    )
    registry_service.complete_run(forecast_run, result_counts={"forecasts": 2})
    forecast_page = registry_service.list_forecasts(limit=10)
    # Ranks are percentiles within a run, so every row reports its run's full size,
    # even when a filter or page shows only part of the run.
    assert [item["cohort_size"] for item in forecast_page["items"]] == [2, 2]
    one_ticker = registry_service.list_forecasts(ticker="event-001", limit=10)
    assert [item["cohort_size"] for item in one_ticker["items"]] == [2]
    assert registry_service.list_forecasts(limit=1)["items"][0]["cohort_size"] == 2

    settlement_run = registry_service.start_run(
        RunRegistration(
            run_type="settlement",
            as_of=AS_OF + timedelta(days=31),
            code_revision="deadbeef",
            config_hash=HASH_C,
            dataset_id="dataset-v2",
            model_id="edgar-moe-v1",
            parent_run_id=forecast_run,
        ),
        run_id="run-settlement-1",
    )
    realized = {"EVENT-001": 0.1, "EVENT-002": -0.05}
    labels = [
        LabelDraft(
            forecast_id=item["forecast_id"],
            source_dataset_id="dataset-v2",
            realized_abnormal_return=realized[item["ticker"]],
            matured_at=AS_OF + timedelta(days=30),
        )
        for item in forecast_page["items"]
    ]
    assert registry_service.settle_labels(settlement_run.run_id, labels)["inserted"] == 2
    registry_service.complete_run(settlement_run.run_id, result_counts={"labels": 2})

    performance = registry_service.performance(model_id="edgar-moe-v1")
    assert performance["forecast_count"] == 2
    assert performance["matured_count"] == 2
    assert performance["coverage"] == 1.0
    assert performance["rank_ic"] == pytest.approx(1.0)
    assert performance["directional_accuracy"] == 1.0
    assert registry_service.status()["pending_count"] == 0
    assert registry_service.list_quality_checks()[0]["status"] == "passed"
    database.dispose()


def test_registry_status_reports_freshness_and_quality_health(tmp_path: Path) -> None:
    database, registry_service = registry(tmp_path)
    run_id = prepare_forecast_run(registry_service)
    registry_service.add_quality_checks(
        run_id,
        [QualityCheckDraft(name="availability", status="passed")],
    )
    registry_service.complete_run(run_id, result_counts={"forecasts": 0})

    recent = registry_service.status(now=datetime.now(UTC) + timedelta(minutes=1))
    assert recent["health_status"] == "ok"
    assert recent["latest_run_status"] == "succeeded"
    assert recent["latest_quality_failures"] == 0
    assert recent["latest_quality_warnings"] == 0
    assert recent["latest_quality_warning_names"] == []
    assert recent["age_seconds"] is not None

    stale = registry_service.status(
        now=datetime.now(UTC) + timedelta(days=5),
    )
    assert stale["health_status"] == "degraded"
    assert "freshness window" in stale["health_message"]
    database.dispose()


def test_local_artifacts_are_content_addressed_and_verified(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    first = store.put_bytes(b"frozen evidence", logical_name="result.json")
    second = store.put_bytes(b"frozen evidence", logical_name="result.json")
    assert first == second
    assert first.key.startswith(f"sha256/{first.sha256[:2]}/{first.sha256}/")
    assert store.read_bytes(first) == b"frozen evidence"

    source = tmp_path / "model.pt"
    source.write_bytes(b"model")
    copied = store.put_file(source)
    assert store.read_bytes(copied) == b"model"


def test_forward_metric_edge_cases_and_percentile_ranks() -> None:
    empty = forward_metrics([], [], forecast_count=3)
    assert empty.pending_count == 3
    assert empty.rank_ic is None
    assert percentile_ranks([2.0, 1.0, 2.0]) == pytest.approx([5 / 6, 1 / 3, 5 / 6])
    assert (empty.rank_ic_low, empty.rank_ic_high) == (None, None)


def test_the_rank_ic_interval_follows_fishers_transform() -> None:
    # Bonett-Wright standard error: sqrt((1 + 0.5**2 / 2) / 25) = 0.21213,
    # so the interval is tanh(atanh(0.5) +/- 1.96 * 0.21213).
    low, high = rank_ic_interval(0.5, 28)
    assert low == pytest.approx(0.1327, abs=1e-4)
    assert high == pytest.approx(0.7465, abs=1e-4)
    # Asymmetric, because the transform compresses near the ends.
    assert 0.5 - low > high - 0.5


def test_a_handful_of_outcomes_cannot_exclude_zero() -> None:
    # The live figure on 2026-09-23: 24 settled outcomes.
    low, high = rank_ic_interval(-0.179, 24)

    assert low is not None and high is not None
    assert low < 0 < high


@pytest.mark.parametrize(
    ("rank_ic", "pair_count", "expected"),
    [
        (None, 40, (None, None)),
        (0.5, 3, (None, None)),  # n - 3 leaves no degrees of freedom
        (0.5, 4, None),  # states an interval, however wide
        (1.0, 30, (1.0, 1.0)),  # Fisher's transform diverges at the ends
        (-1.0, 30, (-1.0, -1.0)),
    ],
)
def test_the_interval_is_withheld_rather_than_invented(
    rank_ic: float | None,
    pair_count: int,
    expected: tuple[float | None, float | None] | None,
) -> None:
    interval = rank_ic_interval(rank_ic, pair_count)

    if expected is None:
        assert interval[0] is not None and interval[1] is not None
        assert interval[0] < interval[1]
    else:
        assert interval == expected


def test_metrics_report_an_interval_alongside_the_rank_ic() -> None:
    metrics = forward_metrics(
        [1.0, 2.0, 3.0, 4.0, 5.0],
        [0.1, 0.3, 0.2, 0.5, 0.4],
        forecast_count=6,
    )

    assert metrics.rank_ic == pytest.approx(0.8)
    assert metrics.rank_ic_low is not None and metrics.rank_ic_high is not None
    assert metrics.rank_ic_low < metrics.rank_ic < metrics.rank_ic_high
    assert metrics.rank_ic_low < 0  # five outcomes settle nothing


def test_status_names_the_checks_that_warned_on_the_latest_run(tmp_path: Path) -> None:
    # The alert classifier needs the names to tell an expected condition from a
    # new problem, and a reader needs them for the same reason.
    database, registry_service = registry(tmp_path)
    run_id = prepare_forecast_run(registry_service)
    registry_service.add_quality_checks(
        run_id,
        [
            QualityCheckDraft(name="availability", status="passed"),
            QualityCheckDraft(name="prospective_candidate_count", status="warning"),
            QualityCheckDraft(name="pre_open_schedule_margin", status="warning"),
        ],
    )
    registry_service.complete_run(run_id, result_counts={"forecasts": 0})

    status = registry_service.status(now=datetime.now(UTC) + timedelta(minutes=1))

    assert status["latest_quality_warnings"] == 2
    assert status["latest_quality_warning_names"] == [
        "pre_open_schedule_margin",
        "prospective_candidate_count",
    ]
    database.dispose()
