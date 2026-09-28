from __future__ import annotations

import importlib
import re

from fastapi.testclient import TestClient

api_module = importlib.import_module("edgar_moe.api.app")


def test_app_process_marker_is_first_once_and_numeric_only(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "_first_app_request_seen", False)
    with TestClient(api_module.app) as client:
        first = client.get("/api/v1/health")
        second = client.get("/api/v1/health")

    first_header = first.headers["X-EDGAR-Read-Timing"]
    second_header = second.headers["X-EDGAR-Read-Timing"]
    assert re.fullmatch(r"app_header_ms=\d+\.\d{3};worker=first", first_header)
    assert re.fullmatch(r"app_header_ms=\d+\.\d{3};worker=subsequent", second_header)
    assert "registry_read_ms" not in first_header
