from __future__ import annotations

from typing import Any

import httpx


class FredClient:
    BASE_URL = "https://api.stlouisfed.org/fred"

    def __init__(
        self, api_key: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not api_key:
            raise ValueError("FRED_API_KEY is required")
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30, transport=transport)

    async def __aenter__(self) -> FredClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    async def observations_as_known(
        self,
        series_id: str,
        observation_start: str,
        observation_end: str,
        as_of: str,
    ) -> list[dict[str, Any]]:
        response = await self.client.get(
            f"{self.BASE_URL}/series/observations",
            params={
                "series_id": series_id,
                "api_key": self.api_key,
                "file_type": "json",
                "observation_start": observation_start,
                "observation_end": observation_end,
                "realtime_start": as_of,
                "realtime_end": as_of,
            },
        )
        response.raise_for_status()
        observations = response.json().get("observations", [])
        return [item for item in observations if item.get("value") not in {None, "."}]
