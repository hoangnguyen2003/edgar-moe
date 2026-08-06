from datetime import date

import httpx
import pytest

from edgar_moe.data.fred import FredApiError, FredClient


async def test_fred_client_requests_initial_release_vintages() -> None:
    observed_query: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_query.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "observations": [
                    {
                        "date": "2020-01-01",
                        "realtime_start": "2020-02-01",
                        "value": "1.5",
                    }
                ]
            },
        )

    async with FredClient("a" * 32, transport=httpx.MockTransport(handler)) as client:
        rows = await client.observations_as_known("TEST", "2020-01-01", "2020-12-31", "2021-01-31")

    assert rows[0]["value"] == "1.5"
    assert observed_query["output_type"] == "4"
    assert observed_query["realtime_start"] == "2020-01-01"
    assert observed_query["realtime_end"] == "2021-01-31"


async def test_fred_client_splits_long_vintage_ranges_and_deduplicates() -> None:
    observed_windows: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        observed_windows.append((params["realtime_start"], params["realtime_end"]))
        return httpx.Response(
            200,
            json={
                "count": 1,
                "observations": [
                    {
                        "date": "2019-01-01",
                        "realtime_start": "2019-01-02",
                        "value": "1.5",
                    }
                ],
            },
        )

    async with FredClient("a" * 32, transport=httpx.MockTransport(handler)) as client:
        rows = await client.observations_as_known("TEST", "2016-01-01", "2026-07-31", "2026-07-31")

    assert len(rows) == 1
    assert len(observed_windows) == 3
    for index, (start, end) in enumerate(observed_windows):
        assert (date.fromisoformat(end) - date.fromisoformat(start)).days < 1_825
        if index:
            previous_end = date.fromisoformat(observed_windows[index - 1][1])
            assert date.fromisoformat(start).toordinal() == previous_end.toordinal() + 1


async def test_fred_client_redacts_key_from_errors() -> None:
    api_key = "credential-that-must-not-appear"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error_message": f"Bad request for api_key={api_key}"},
        )

    async with FredClient(api_key, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FredApiError) as captured:
            await client.observations_as_known("TEST", "2020-01-01", "2020-12-31", "2021-01-31")

    assert api_key not in str(captured.value)
    assert "<redacted>" in str(captured.value)
