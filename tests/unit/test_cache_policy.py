"""What may be cached, by whom, and for how long.

Two mistakes cost real time here: a snapshot read with no ``s-maxage`` makes
every visitor invoke the function, and a content-hashed asset served with
``must-revalidate`` makes a returning reader ask about every file it already
has. Both are invisible until measured, so they are asserted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
VERCEL_CONFIG = REPOSITORY / "vercel.json"

_DIRECTIVE = re.compile(r"(?P<name>[a-z-]+)(?:=(?P<value>\d+))?")


def directives(header: str) -> dict[str, int | None]:
    return {
        match.group("name"): int(match.group("value")) if match.group("value") else None
        for match in _DIRECTIVE.finditer(header)
    }


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from edgar_moe.api.app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "route",
    [
        "/api/v1/summary",
        "/api/v1/experiments",
        "/api/v1/equity-curves",
        "/api/v1/events",
        "/api/v1/latest-signals",
        "/api/v1/methodology",
    ],
)
def test_a_snapshot_read_is_cacheable_by_the_edge(client, route: str) -> None:
    header = client.get(route).headers["cache-control"]
    policy = directives(header)

    assert "public" in policy
    # Without s-maxage the edge does not cache a function response at all.
    assert policy["s-maxage"] == 86_400
    assert policy["stale-while-revalidate"] == 604_800
    assert policy["max-age"] is not None and policy["max-age"] <= 3_600


def test_live_registry_reads_expire_quickly_everywhere(client) -> None:
    response = client.get("/api/v1/forward/forecasts")
    if response.status_code != 200:  # pragma: no cover - only when no registry is configured
        pytest.skip("forward registry is not configured in this environment")
    policy = directives(response.headers["cache-control"])

    assert policy["max-age"] == 60
    assert policy["s-maxage"] == 60
    assert policy["stale-while-revalidate"] == 300


def test_operational_state_is_never_cached(client) -> None:
    for route in ("/api/v1/health", "/api/v1/freshness", "/api/v1/forward/status"):
        header = client.get(route).headers["cache-control"]
        assert header in {"no-store", "no-cache"}, f"{route} sent {header}"


def test_the_cache_helpers_cannot_drift_apart() -> None:
    from edgar_moe.api import app as api

    assert api._EDGE_LIVE_SECONDS < api._EDGE_SNAPSHOT_SECONDS
    assert api._EDGE_LIVE_STALE_SECONDS < api._EDGE_SNAPSHOT_STALE_SECONDS


def _header_rules() -> list[dict[str, object]]:
    return json.loads(VERCEL_CONFIG.read_text(encoding="utf-8"))["headers"]


def test_content_hashed_assets_are_immutable() -> None:
    rule = next(item for item in _header_rules() if item["source"] == "/assets/(.*)")
    value = next(
        header["value"]
        for header in rule["headers"]  # type: ignore[union-attr]
        if header["key"] == "Cache-Control"
    )
    policy = directives(value)

    assert "immutable" in policy
    assert policy["max-age"] == 31_536_000


def test_no_rule_makes_the_entry_document_immutable() -> None:
    # index.html names the current asset files; a stale copy points at files a
    # deployment has already replaced.
    for rule in _header_rules():
        source = str(rule["source"])
        if source.startswith("/assets/"):
            continue
        for header in rule["headers"]:  # type: ignore[union-attr]
            assert header["key"] != "Cache-Control", f"{source} sets Cache-Control"


def test_every_security_header_still_applies_to_every_path() -> None:
    catch_all = next(item for item in _header_rules() if item["source"] == "/(.*)")
    keys = {header["key"] for header in catch_all["headers"]}  # type: ignore[union-attr]

    assert {
        "Content-Security-Policy",
        "Strict-Transport-Security",
        "X-Content-Type-Options",
    } <= keys
