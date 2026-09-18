from __future__ import annotations

from edgar_moe.api.app import _api_registry_database_url
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
