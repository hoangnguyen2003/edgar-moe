from __future__ import annotations

import pytest

from edgar_moe.forward.database import RegistryDatabase


def test_sqlite_ignores_serverless_pool_options(tmp_path) -> None:
    database = RegistryDatabase(
        f"sqlite:///{tmp_path / 'registry.sqlite3'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )

    try:
        database.create_schema()
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
    finally:
        database.dispose()


def test_postgres_applies_serverless_pool_options_without_connecting() -> None:
    database = RegistryDatabase(
        "postgresql://reader:password@db.example.test/edgar_moe",
        pool_size=2,
        max_overflow=1,
        pool_timeout=4.5,
    )

    try:
        pool = database.engine.pool
        assert pool.size() == 2
        assert pool._max_overflow == 1
        assert pool._timeout == 4.5
    finally:
        database.dispose()


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("pool_size", 0, "pool_size must be at least 1"),
        ("max_overflow", -1, "max_overflow must be non-negative"),
        ("pool_timeout", 0, "pool_timeout must be greater than 0"),
    ],
)
def test_pool_options_reject_invalid_values(option: str, value: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RegistryDatabase("sqlite:///:memory:", **{option: value})
