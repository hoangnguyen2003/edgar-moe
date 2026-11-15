from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from edgar_moe.forward.database import normalize_database_url


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


@pytest.mark.parametrize("name", ["encoded%2Foption", "literal%percent", "encoded%40password"])
def test_migration_environment_preserves_percent_encoded_urls(tmp_path, monkeypatch, name):
    database_path = tmp_path / f"{name}.sqlite3"
    decoded_path = Path(unquote(str(database_path)))
    decoded_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{database_path}"
    monkeypatch.setenv("EDGAR_MOE_REGISTRY_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)
    assert config.get_main_option("sqlalchemy.url") == url
    assert decoded_path.is_file()


def test_postgres_percent_encoded_credentials_and_tls_options_round_trip_offline(
    monkeypatch, capsys
):
    url = "postgresql://reader:synthetic%40secret@example.test/db?sslrootcert=%2Fca.pem"
    monkeypatch.setenv("EDGAR_MOE_REGISTRY_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head", sql=True)
    assert config.get_main_option("sqlalchemy.url") == normalize_database_url(url)
    assert url not in capsys.readouterr().out
