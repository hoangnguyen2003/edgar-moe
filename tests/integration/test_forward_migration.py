from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_forward_registry_migration_round_trip(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    tables = set(inspect(engine).get_table_names())
    assert {
        "forward_datasets",
        "forward_models",
        "forward_runs",
        "forward_forecasts",
        "forward_labels",
        "forward_artifacts",
        "forward_data_quality_checks",
        "forward_audit_events",
    }.issubset(tables)

    command.downgrade(config, "base")
    remaining = set(inspect(engine).get_table_names())
    assert not any(name.startswith("forward_") for name in remaining)
    engine.dispose()
