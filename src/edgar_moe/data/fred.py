from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class FredApiError(RuntimeError):
    """A FRED failure whose message never includes the credential-bearing URL."""


class FredRetryableError(FredApiError):
    """A transient FRED response that is safe to retry."""


class FredClient:
    BASE_URL = "https://api.stlouisfed.org/fred"
    # FRED rejects JSON responses spanning more than 2,000 vintage dates. A
    # 1,825-calendar-day window is safe even for a series with daily vintages.
    MAX_VINTAGE_WINDOW_DAYS = 1_825
    PAGE_LIMIT = 100_000

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not api_key:
            raise ValueError("FRED_API_KEY is required")
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30, transport=transport)

    async def __aenter__(self) -> FredClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    def _safe_error_detail(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            detail = response.text.strip()
        else:
            detail = str(payload.get("error_message", "")) if isinstance(payload, dict) else ""
        detail = detail or "No error detail returned"
        return detail.replace(self.api_key, "<redacted>")[:1_000]

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, FredRetryableError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
    )
    async def _observations_window(
        self,
        series_id: str,
        observation_start: str,
        observation_end: str,
        realtime_start: str,
        realtime_end: str,
    ) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = await self.client.get(
                f"{self.BASE_URL}/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": self.api_key,
                    "file_type": "json",
                    "observation_start": observation_start,
                    "observation_end": observation_end,
                    "realtime_start": realtime_start,
                    "realtime_end": realtime_end,
                    "output_type": 4,
                    "limit": self.PAGE_LIMIT,
                    "offset": offset,
                },
            )
            if response.is_error:
                error_type = (
                    FredRetryableError
                    if response.status_code == 429 or response.status_code >= 500
                    else FredApiError
                )
                raise error_type(
                    f"FRED API request for {series_id} failed with HTTP "
                    f"{response.status_code}: {self._safe_error_detail(response)}"
                ) from None
            try:
                payload = response.json()
            except ValueError as error:
                raise FredApiError(
                    f"FRED API request for {series_id} returned invalid JSON"
                ) from error
            page = payload.get("observations", [])
            if not isinstance(page, list):
                raise FredApiError(
                    f"FRED API request for {series_id} returned invalid observations"
                )
            observations.extend(item for item in page if isinstance(item, dict))
            offset += len(page)
            count = int(payload.get("count", offset))
            if not page or offset >= count:
                return observations

    async def observations_as_known(
        self,
        series_id: str,
        observation_start: str,
        observation_end: str,
        as_of: str,
    ) -> list[dict[str, Any]]:
        """Return initial releases with their actual point-in-time availability dates."""
        vintage_start = date.fromisoformat(observation_start)
        vintage_end = date.fromisoformat(as_of)
        if vintage_end < vintage_start:
            raise ValueError("as_of must not be earlier than observation_start")

        rows: list[dict[str, Any]] = []
        cursor = vintage_start
        window_delta = timedelta(days=self.MAX_VINTAGE_WINDOW_DAYS - 1)
        while cursor <= vintage_end:
            window_end = min(cursor + window_delta, vintage_end)
            rows.extend(
                await self._observations_window(
                    series_id,
                    observation_start,
                    observation_end,
                    cursor.isoformat(),
                    window_end.isoformat(),
                )
            )
            cursor = window_end + timedelta(days=1)

        unique = {
            (
                str(item.get("date", "")),
                str(item.get("realtime_start", "")),
                str(item.get("realtime_end", "")),
                str(item.get("value", "")),
            ): item
            for item in rows
            if item.get("value") not in {None, "."}
        }
        return [unique[key] for key in sorted(unique)]
