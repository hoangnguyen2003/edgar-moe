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
    assert settings.edgar_moe_registry_api_statement_timeout_ms == 5_000


def test_copilot_retry_settings_are_bounded_and_configurable(monkeypatch) -> None:
    monkeypatch.setenv("EDGAR_MOE_COPILOT_MAX_RETRIES", "3")
    monkeypatch.setenv("EDGAR_MOE_COPILOT_RETRY_BACKOFF_SECONDS", "1.5")

    settings = RuntimeSettings(_env_file=None)

    assert settings.edgar_moe_copilot_max_retries == 3
    assert settings.edgar_moe_copilot_retry_backoff_seconds == 1.5


def test_copilot_provider_host_allowlist_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv(
        "EDGAR_MOE_COPILOT_ALLOWED_HOSTS",
        "api.openai.com, api.example.com",
    )

    settings = RuntimeSettings(_env_file=None)

    assert settings.edgar_moe_copilot_allowed_hosts == "api.openai.com, api.example.com"


def test_copilot_duration_budget_is_bounded_and_configurable(monkeypatch) -> None:
    monkeypatch.setenv("EDGAR_MOE_COPILOT_MAX_DURATION_SECONDS", "450")

    settings = RuntimeSettings(_env_file=None)

    assert settings.edgar_moe_copilot_max_duration_seconds == 450


def test_copilot_context_budget_is_bounded_and_configurable(monkeypatch) -> None:
    monkeypatch.setenv("EDGAR_MOE_COPILOT_MAX_CONTEXT_BYTES", "1048576")

    settings = RuntimeSettings(_env_file=None)

    assert settings.edgar_moe_copilot_max_context_bytes == 1_048_576
