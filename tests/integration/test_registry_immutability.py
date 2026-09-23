from __future__ import annotations

import hashlib
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from edgar_moe.forward.artifacts import ArtifactReference
from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.domain import (
    DatasetRegistration,
    ForecastDraft,
    LabelDraft,
    ModelRegistration,
    QualityCheckDraft,
    RunRegistration,
)
from edgar_moe.forward.immutability import append_only_statements
from edgar_moe.forward.registry import ForwardRegistry

HASH = "a" * 64


def test_append_only_statements_are_pinned() -> None:
    # Migration 20260921_0002 applies these statements. Changing them requires a
    # new Alembic revision; update these digests together with that migration.
    expected = {
        "postgresql": "b302bd11a7bb12d655b2b7fa88fc8a32fa60105a03d26d131fae67203dd42cde",
        "sqlite": "0d34243720e9d014549e43025de47a73a392ce692e98e44b81ca07428ed449f2",
    }
    for dialect, digest in expected.items():
        statements = "\n".join(append_only_statements(dialect))
        assert hashlib.sha256(statements.encode()).hexdigest() == digest


def _seed(database: RegistryDatabase, *, suffix: str = "1") -> tuple[ForwardRegistry, str]:
    dataset_id = f"dataset-{suffix}"
    model_id = f"model-{suffix}"
    registry = ForwardRegistry(database, actor="immutability-test")
    registry.register_dataset(
        DatasetRegistration(
            dataset_id=dataset_id,
            as_of=date(2026, 9, 21),
            source_manifest_hash=HASH,
            manifest_sha256=HASH,
            row_counts={"events": 1},
        )
    )
    registry.register_model(
        ModelRegistration(
            model_id=model_id,
            name="Frozen MoE",
            version="1",
            training_dataset_id=dataset_id,
            selection_hash=HASH,
            artifact_uri="local://model.pt",
            artifact_sha256=HASH,
            config_hash=HASH,
            champion_family="multimodal",
            frozen_at=datetime(2026, 8, 6, tzinfo=UTC),
        )
    )
    now = datetime.now(UTC)
    run = registry.start_run(
        RunRegistration(
            run_type="forecast",
            as_of=now,
            code_revision="test",
            config_hash=HASH,
            dataset_id=dataset_id,
            model_id=model_id,
        )
    )
    registry.append_forecasts(
        run.run_id,
        [
            ForecastDraft(
                event_id=f"event-{suffix}",
                accession_number=f"0000000000-26-{int(suffix[:8], 16) % 1_000_000:06d}",
                security_id=f"asset-{suffix}",
                ticker="TEST",
                company_name="Test Corp",
                form="10-Q",
                accepted_at=now - timedelta(hours=1),
                entry_at=now + timedelta(hours=6),
                entry_date=(now + timedelta(days=1)).date(),
                horizon_at=now + timedelta(days=30),
                industry_code="3571",
                score=0.1,
                rank=1.0,
            )
        ],
    )
    registry.complete_run(run.run_id, result_counts={"forecasts": 1})
    return registry, run.run_id


def test_postgres_migrated_append_only_triggers_reject_direct_sql_mutations() -> None:
    database_url = os.environ.get("EDGAR_MOE_TEST_POSTGRES_URL", "").strip()
    if not database_url:
        pytest.skip("EDGAR_MOE_TEST_POSTGRES_URL must target the disposable trigger-test database")
    parsed_url = make_url(database_url)
    if (
        parsed_url.drivername.split("+")[0] not in {"postgres", "postgresql"}
        or parsed_url.host not in {"127.0.0.1", "localhost", "::1"}
        or parsed_url.database != "edgar_moe_immutability_test"
    ):
        pytest.fail(
            "PostgreSQL trigger integration tests require the dedicated loopback test database"
        )

    database = RegistryDatabase(database_url)
    try:
        protected_tables = (
            "forward_datasets",
            "forward_models",
            "forward_forecasts",
            "forward_labels",
            "forward_artifacts",
            "forward_data_quality_checks",
            "forward_audit_events",
            "forward_runs",
        )
        with database.engine.connect() as connection:
            existing_rows = {
                table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in protected_tables
            }
        if any(existing_rows.values()):
            pytest.fail("The dedicated PostgreSQL trigger-test database must be empty")

        suffix = uuid4().hex
        registry, forecast_run_id = _seed(database, suffix=suffix)
        dataset_id = f"dataset-{suffix}"
        model_id = f"model-{suffix}"
        with database.engine.connect() as connection:
            forecast_id, horizon_at = connection.execute(
                text(
                    "SELECT forecast_id, horizon_at FROM forward_forecasts WHERE run_id = :run_id"
                ),
                {"run_id": forecast_run_id},
            ).one()

        verification_run = registry.start_run(
            RunRegistration(
                run_type="verification",
                as_of=datetime.now(UTC),
                code_revision="test",
                config_hash=HASH,
                dataset_id=dataset_id,
                model_id=model_id,
            )
        )
        registry.add_quality_checks(
            verification_run.run_id,
            [QualityCheckDraft(name="trigger-fixture", status="passed")],
        )
        registry.register_artifact(
            verification_run.run_id,
            kind="trigger-fixture",
            reference=ArtifactReference(
                uri=f"local://trigger-fixture/{suffix}",
                sha256=HASH,
                size_bytes=1,
                key=f"trigger-fixture/{suffix}",
            ),
        )
        registry.complete_run(verification_run.run_id, result_counts={})

        settlement_run = registry.start_run(
            RunRegistration(
                run_type="settlement",
                as_of=horizon_at + timedelta(days=1),
                code_revision="test",
                config_hash=HASH,
                dataset_id=dataset_id,
                model_id=model_id,
            )
        )
        registry.settle_labels(
            settlement_run.run_id,
            [
                LabelDraft(
                    forecast_id=forecast_id,
                    source_dataset_id=dataset_id,
                    realized_abnormal_return=0.02,
                    matured_at=horizon_at,
                )
            ],
        )
        registry.complete_run(settlement_run.run_id, result_counts={"labels": 1})

        update_columns = {
            "forward_datasets": "provenance = provenance",
            "forward_models": "name = name",
            "forward_forecasts": "score = score",
            "forward_labels": "realized_abnormal_return = realized_abnormal_return",
            "forward_artifacts": "kind = kind",
            "forward_data_quality_checks": "status = status",
            "forward_audit_events": "actor = actor",
        }
        mutations = [
            (f"UPDATE {table} SET {column}", {}) for table, column in update_columns.items()
        ]
        mutations.extend((f"DELETE FROM {table}", {}) for table in update_columns)
        mutations.extend((f"TRUNCATE TABLE {table} CASCADE", {}) for table in protected_tables)
        mutations.extend(
            [
                (
                    "UPDATE forward_runs SET status = 'running' WHERE run_id = :run_id",
                    {"run_id": forecast_run_id},
                ),
                ("DELETE FROM forward_runs", {}),
            ]
        )
        for statement, parameters in mutations:
            with database.engine.connect() as connection:
                transaction = connection.begin()
                try:
                    with pytest.raises(IntegrityError, match="append-only|outcome once"):
                        connection.execute(text(statement), parameters)
                finally:
                    transaction.rollback()

        with database.engine.connect() as connection:
            final_counts = {
                table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in protected_tables
            }
            final_statuses = (
                connection.execute(
                    text("SELECT status FROM forward_runs ORDER BY started_at, run_id")
                )
                .scalars()
                .all()
            )
        assert all(final_counts[table] > 0 for table in protected_tables)
        assert final_counts["forward_forecasts"] == 1
        assert final_counts["forward_labels"] == 1
        assert final_counts["forward_artifacts"] == 1
        assert final_counts["forward_data_quality_checks"] == 1
        assert len(final_statuses) == 3
        assert set(final_statuses) == {"succeeded"}
    finally:
        database.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE forward_forecasts SET score = 9",
        "DELETE FROM forward_forecasts",
        "UPDATE forward_audit_events SET actor = 'someone else'",
        "DELETE FROM forward_datasets",
        "DELETE FROM forward_runs",
        "UPDATE forward_runs SET status = 'running'",
    ],
)
def test_sql_cannot_rewrite_evidence_or_completed_runs(tmp_path: Path, statement: str) -> None:
    database = RegistryDatabase(f"sqlite:///{tmp_path / 'registry.sqlite3'}")
    database.create_schema()
    _seed(database)

    with (
        pytest.raises(IntegrityError, match="append-only|outcome once"),
        database.engine.begin() as connection,
    ):
        connection.execute(text(statement))
    database.dispose()


def test_a_running_run_records_only_its_outcome(tmp_path: Path) -> None:
    database = RegistryDatabase(f"sqlite:///{tmp_path / 'registry.sqlite3'}")
    database.create_schema()
    database.create_schema()  # idempotent, like forward-init after migrations
    registry, _ = _seed(database)
    running = registry.start_run(
        RunRegistration(
            run_type="settlement",
            as_of=datetime.now(UTC),
            code_revision="test",
            config_hash=HASH,
            dataset_id="dataset-1",
            model_id="model-1",
        )
    )

    with pytest.raises(IntegrityError, match="outcome once"), database.engine.begin() as connection:
        connection.execute(
            text("UPDATE forward_runs SET code_revision = 'rewritten' WHERE run_id = :run_id"),
            {"run_id": running.run_id},
        )
    registry.fail_run(running.run_id, error_message="recorded once")
    assert registry.list_runs(limit=1)[0]["status"] == "failed"
    database.dispose()


def test_migration_installs_the_same_triggers_as_create_schema(tmp_path: Path) -> None:
    migrated_path = tmp_path / "migrated.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{migrated_path}")
    command.upgrade(config, "head")
    created = RegistryDatabase(f"sqlite:///{tmp_path / 'created.sqlite3'}")
    created.create_schema()

    query = text("SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name")
    migrated_engine = create_engine(f"sqlite:///{migrated_path}")
    with migrated_engine.connect() as connection:
        migrated = connection.execute(query).all()
    with created.engine.connect() as connection:
        from_schema = connection.execute(query).all()
    assert migrated == from_schema
    assert len(migrated) == 16

    command.downgrade(config, "20260806_0001")
    with migrated_engine.connect() as connection:
        assert connection.execute(query).all() == []
    migrated_engine.dispose()
    created.dispose()
