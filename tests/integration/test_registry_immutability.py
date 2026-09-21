from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.domain import (
    DatasetRegistration,
    ForecastDraft,
    ModelRegistration,
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


def _seed(database: RegistryDatabase) -> tuple[ForwardRegistry, str]:
    registry = ForwardRegistry(database, actor="immutability-test")
    registry.register_dataset(
        DatasetRegistration(
            dataset_id="dataset-1",
            as_of=date(2026, 9, 21),
            source_manifest_hash=HASH,
            manifest_sha256=HASH,
            row_counts={"events": 1},
        )
    )
    registry.register_model(
        ModelRegistration(
            model_id="model-1",
            name="Frozen MoE",
            version="1",
            training_dataset_id="dataset-1",
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
            dataset_id="dataset-1",
            model_id="model-1",
        )
    )
    registry.append_forecasts(
        run.run_id,
        [
            ForecastDraft(
                event_id="event-1",
                accession_number="0000000000-26-000001",
                security_id="asset-1",
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

    with pytest.raises(IntegrityError, match="append-only|outcome once"), database.engine.begin() as connection:
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
