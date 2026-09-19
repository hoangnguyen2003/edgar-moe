from __future__ import annotations

from edgar_moe.settings import RuntimeSettings


def test_registry_reader_url_is_a_separate_runtime_setting(monkeypatch) -> None:
    monkeypatch.setenv("EDGAR_MOE_REGISTRY_DATABASE_URL", "postgresql://writer")
    monkeypatch.setenv("EDGAR_MOE_REGISTRY_READ_DATABASE_URL", "postgresql://reader")

    settings = RuntimeSettings(_env_file=None)

    assert settings.edgar_moe_registry_database_url == "postgresql://writer"
    assert settings.edgar_moe_registry_read_database_url == "postgresql://reader"


def test_api_pool_settings_are_bounded_and_configurable(monkeypatch) -> None:
    monkeypatch.setenv("EDGAR_MOE_REGISTRY_API_POOL_SIZE", "3")
    monkeypatch.setenv("EDGAR_MOE_REGISTRY_API_MAX_OVERFLOW", "2")
    monkeypatch.setenv("EDGAR_MOE_REGISTRY_API_POOL_TIMEOUT_SECONDS", "7.5")

    settings = RuntimeSettings(_env_file=None)

    assert settings.edgar_moe_registry_api_pool_size == 3
    assert settings.edgar_moe_registry_api_max_overflow == 2
    assert settings.edgar_moe_registry_api_pool_timeout_seconds == 7.5
