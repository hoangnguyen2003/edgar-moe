from datetime import date

import httpx

from edgar_moe.data.alpaca import AlpacaDataClient


async def test_daily_bars_use_split_adjustment_and_cutoff_symbol_mapping() -> None:
    observed_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_queries.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "bars": {"AAPL": [{"t": "2025-01-02T05:00:00Z", "c": 100.0}]},
                "next_page_token": None,
            },
        )

    async with AlpacaDataClient("key", "secret", transport=httpx.MockTransport(handler)) as client:
        rows = await client.daily_bars(["AAPL"], date(2020, 1, 1), date(2025, 12, 31))

    assert rows["AAPL"][0]["c"] == 100.0
    assert observed_queries[0]["adjustment"] == "split"
    assert observed_queries[0]["asof"] == "2025-12-31"


async def test_corporate_actions_merge_nested_pages() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if "page_token" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "corporate_actions": {"cash_dividends": [{"id": "one"}]},
                    "next_page_token": "next",
                },
            )
        return httpx.Response(
            200,
            json={
                "corporate_actions": {
                    "cash_dividends": [{"id": "two"}],
                    "forward_splits": [{"id": "three"}],
                }
            },
        )

    async with AlpacaDataClient("key", "secret", transport=httpx.MockTransport(handler)) as client:
        payload = await client.corporate_actions(date(2025, 1, 1), date(2025, 12, 31), ["AAPL"])

    assert calls == 2
    assert [item["id"] for item in payload["corporate_actions"]["cash_dividends"]] == [
        "one",
        "two",
    ]
    assert payload["corporate_actions"]["forward_splits"][0]["id"] == "three"
