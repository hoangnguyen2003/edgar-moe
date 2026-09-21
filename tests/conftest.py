"""Keep tests independent of a developer's local .env and .env.local files."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from edgar_moe.settings import runtime_settings

# Settings a local .env.local may fill with real credentials or production URLs.
# CI never has them, so no test may pick them up locally either; environment
# variables take precedence over the dotenv files.
_ISOLATED_SETTINGS = {
    "EDGAR_MOE_REGISTRY_DATABASE_URL": "",
    "EDGAR_MOE_REGISTRY_READ_DATABASE_URL": "",
    "EDGAR_MOE_ARTIFACT_BACKEND": "local",
    "EDGAR_MOE_ARTIFACT_MIRROR_BACKEND": "none",
    "EDGAR_MOE_R2_ENDPOINT_URL": "",
    "EDGAR_MOE_R2_BUCKET": "",
    "EDGAR_MOE_R2_ACCESS_KEY_ID": "",
    "EDGAR_MOE_R2_SECRET_ACCESS_KEY": "",
    "EDGAR_MOE_COPILOT_API_KEY": "",
    "EDGAR_MOE_ALERT_WEBHOOK_URL": "",
    "ALPACA_API_KEY": "",
    "ALPACA_API_SECRET": "",
    "FRED_API_KEY": "",
    "SEC_USER_AGENT": "EDGAR-MoE Research research@example.com",
}


@pytest.fixture(autouse=True)
def isolated_runtime_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name, value in _ISOLATED_SETTINGS.items():
        monkeypatch.setenv(name, value)
    runtime_settings.cache_clear()
    yield
    runtime_settings.cache_clear()
