from __future__ import annotations

from datetime import date
from typing import Any, cast

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


class AlpacaDataClient:
    BASE_URL = "https://data.alpaca.markets"
    TRADING_URL = "https://paper-api.alpaca.markets"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Alpaca API credentials are required")
        self.client = httpx.AsyncClient(
            headers={
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
            },
            timeout=45,
            transport=transport,
        )

    async def __aenter__(self) -> AlpacaDataClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def assets(self, status: str | None = None) -> list[dict[str, Any]]:
        params = {"asset_class": "us_equity"}
        if status:
            params["status"] = status
        payload = await self._get(f"{self.TRADING_URL}/v2/assets", params)
        if not isinstance(payload, list):
            raise ValueError("Unexpected Alpaca assets response")
        return payload

    async def daily_bars(
        self,
        symbols: list[str],
        start: date,
        end: date,
        feed: str = "iex",
    ) -> dict[str, list[dict[str, Any]]]:
        params: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": start.isoformat(),
            "end": end.isoformat(),
            # Split adjustment removes mechanical price jumps while avoiding
            # dividend/spin-off restatements in historical feature levels.
            "adjustment": "split",
            # Resolve symbol changes as they were known at the research cutoff.
            "asof": end.isoformat(),
            "feed": feed,
            "limit": 10_000,
        }
        all_bars: dict[str, list[dict[str, Any]]] = {}
        while True:
            payload = await self._get(f"{self.BASE_URL}/v2/stocks/bars", params)
            for symbol, rows in payload.get("bars", {}).items():
                all_bars.setdefault(symbol, []).extend(rows)
            token = payload.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        return all_bars

    async def corporate_actions(
        self, start: date, end: date, symbols: list[str] | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "sort": "asc",
            "limit": 1000,
        }
        if symbols:
            params["symbols"] = ",".join(symbols)
        combined: dict[str, Any] = {}
        while True:
            payload = await self._get(f"{self.BASE_URL}/v1/corporate-actions", params)
            for key, value in payload.items():
                if key == "next_page_token":
                    continue
                _merge_payload_value(combined, key, value)
            token = payload.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        return combined


def _merge_payload_value(destination: dict[str, Any], key: str, value: Any) -> None:
    if isinstance(value, list):
        destination.setdefault(key, []).extend(value)
        return
    if isinstance(value, dict):
        nested = destination.setdefault(key, {})
        if not isinstance(nested, dict):
            raise ValueError(f"Corporate-action response changed type for {key}")
        for nested_key, nested_value in value.items():
            _merge_payload_value(nested, nested_key, nested_value)
        return
    if key not in destination:
        destination[key] = value
