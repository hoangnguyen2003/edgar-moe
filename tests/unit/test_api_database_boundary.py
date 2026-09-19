from __future__ import annotations

from edgar_moe.api.app import _api_registry_database_url, _build_api_registry_database
from edgar_moe.settings import RuntimeSettings


def test_api_database_url_prefers_select_only_reader() -> None:
    settings = RuntimeSettings(
        _env_file=None,
        edgar_moe_registry_database_url="postgresql://writer",
        edgar_moe_registry_read_database_url="postgresql://reader",
    )
    assert _api_registry_database_url(settings) == "postgresql://reader"


def test_api_database_url_keeps_local_compatibility_fallback() -> None:
    settings = RuntimeSettings(
        _env_file=None,
        edgar_moe_registry_database_url="sqlite:///local.db",
        edgar_moe_registry_read_database_url="",
    )
    assert _api_registry_database_url(settings) == "sqlite:///local.db"


def test_api_database_url_does_not_fallback_to_a_postgres_writer() -> None:
    settings = RuntimeSettings(
        _env_file=None,
        edgar_moe_registry_database_url="postgresql://writer",
        edgar_moe_registry_read_database_url="",
    )
    assert _api_registry_database_url(settings) == ""


def test_api_database_passes_bounded_pool_settings_to_reader(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeDatabase:
        def __init__(self, database_url: str, **kwargs: object) -> None:
            captured["database_url"] = database_url
            captured.update(kwargs)

    monkeypatch.setattr("edgar_moe.api.app.RegistryDatabase", FakeDatabase)
    settings = RuntimeSettings(
        _env_file=None,
        edgar_moe_registry_read_database_url="postgresql://reader",
        edgar_moe_registry_api_pool_size=2,
        edgar_moe_registry_api_max_overflow=1,
        edgar_moe_registry_api_pool_timeout_seconds=4.5,
    )

    database = _build_api_registry_database(settings)

    assert isinstance(database, FakeDatabase)
    assert captured == {
        "database_url": "postgresql://reader",
        "pool_size": 2,
        "max_overflow": 1,
        "pool_timeout": 4.5,
    }


def test_api_database_does_not_open_when_no_reader_is_configured() -> None:
    settings = RuntimeSettings(_env_file=None)
    assert _build_api_registry_database(settings) is None
