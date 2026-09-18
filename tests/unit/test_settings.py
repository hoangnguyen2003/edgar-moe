from __future__ import annotations

from edgar_moe.settings import RuntimeSettings


def test_registry_reader_url_is_a_separate_runtime_setting(monkeypatch) -> None:
    monkeypatch.setenv("EDGAR_MOE_REGISTRY_DATABASE_URL", "postgresql://writer")
    monkeypatch.setenv("EDGAR_MOE_REGISTRY_READ_DATABASE_URL", "postgresql://reader")

    settings = RuntimeSettings(_env_file=None)

    assert settings.edgar_moe_registry_database_url == "postgresql://writer"
    assert settings.edgar_moe_registry_read_database_url == "postgresql://reader"
