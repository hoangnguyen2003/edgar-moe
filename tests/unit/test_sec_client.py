"""Offline tests for the EDGAR client's request behaviour.

The client is the project's only contact with the SEC, and the rules it has to
respect are the SEC's, not ours: identify yourself, stay inside the fair-access
rate, and ask only for the documents you named. None of that was covered,
although the constructor already accepts a transport so it can be driven
without a network. Every test here uses one.
"""

from __future__ import annotations

import asyncio
import gzip
from pathlib import Path

import httpx
import pytest

from edgar_moe.data.sec import SecClient


def _transport(
    handler: object = None,
    *,
    payload: dict[str, object] | None = None,
    text: str | None = None,
) -> httpx.MockTransport:
    requested: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        if callable(handler):
            return handler(request)
        if text is not None:
            return httpx.Response(200, text=text)
        return httpx.Response(200, json=payload or {})

    transport = httpx.MockTransport(respond)
    transport.requested = requested  # type: ignore[attr-defined]
    return transport


def _client(tmp_path: Path, transport: httpx.MockTransport, **options: object) -> SecClient:
    return SecClient(
        user_agent="EDGAR-MoE research (contact@example.com)",
        cache_dir=tmp_path / "cache",
        transport=transport,
        **options,  # type: ignore[arg-type]
    )


def test_a_user_agent_without_a_contact_is_refused(tmp_path: Path) -> None:
    # The SEC requires a contact address; a client without one gets blocked,
    # and the failure would arrive as opaque 403s in the middle of an ingest.
    with pytest.raises(ValueError, match="contact email"):
        SecClient(user_agent="EDGAR-MoE research", cache_dir=tmp_path)


@pytest.mark.parametrize(
    ("requested_rate", "expected_interval"),
    [(1, 1.0), (4, 0.25), (8, 0.125), (50, 0.125), (0, 1.0), (-3, 1.0)],
)
def test_the_request_rate_is_clamped_to_the_fair_access_limit(
    tmp_path: Path,
    requested_rate: int,
    expected_interval: float,
) -> None:
    client = _client(tmp_path, _transport(), requests_per_second=requested_rate)

    assert client.minimum_interval == pytest.approx(expected_interval)


def test_requests_are_paced_rather_than_sent_at_once(tmp_path: Path) -> None:
    transport = _transport(payload={"ok": True})

    async def exercise() -> float:
        client = _client(tmp_path, transport, requests_per_second=8)
        async with client:
            loop = asyncio.get_running_loop()
            started = loop.time()
            for _ in range(3):
                await client.company_facts("320193")
            return loop.time() - started

    elapsed = asyncio.run(exercise())

    # Three requests at eight per second cannot finish faster than two gaps.
    assert elapsed >= 0.25
    assert len(transport.requested) == 3  # type: ignore[attr-defined]


def test_a_filer_identifier_is_zero_padded_into_the_documented_urls(tmp_path: Path) -> None:
    transport = _transport(payload={"cik": "320193"})

    async def exercise() -> None:
        async with _client(tmp_path, transport) as client:
            await client.submissions("320193")
            await client.company_facts("320193")

    asyncio.run(exercise())
    urls = [str(request.url) for request in transport.requested]  # type: ignore[attr-defined]

    assert urls == [
        "https://data.sec.gov/submissions/CIK0000320193.json",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
    ]


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "CIK0000320193-submissions-001.json.bak",
        "CIK320193-submissions-001.json",
        "submissions.json",
        "CIK0000320193-submissions-1.json",
    ],
)
def test_a_history_filename_the_sec_did_not_publish_is_refused(tmp_path: Path, name: str) -> None:
    # The name arrives inside a fetched payload, so it is attacker-adjacent
    # input that is about to be pasted into a URL.
    transport = _transport()

    async def exercise() -> None:
        async with _client(tmp_path, transport) as client:
            await client.submission_file(name)

    with pytest.raises(ValueError, match="Unexpected SEC submissions filename"):
        asyncio.run(exercise())
    assert transport.requested == []  # type: ignore[attr-defined]


def test_history_files_are_followed_and_merged_into_the_recent_rows(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("CIK0000320193.json"):
            return httpx.Response(
                200,
                json={
                    "filings": {
                        "recent": {"accessionNumber": ["a-1"], "form": ["10-K"]},
                        "files": [
                            {
                                "name": "CIK0000320193-submissions-001.json",
                                "filingTo": "2020-12-31",
                            },
                            {
                                "name": "CIK0000320193-submissions-002.json",
                                "filingTo": "2015-12-31",
                            },
                            {"filingTo": "2019-12-31"},
                            "not-a-descriptor",
                        ],
                    }
                },
            )
        return httpx.Response(
            200, json={"accessionNumber": [f"h-{request.url.path[-8:-5]}"], "form": ["10-Q"]}
        )

    transport = _transport(respond)

    async def exercise() -> dict[str, object]:
        async with _client(tmp_path, transport) as client:
            return await client.complete_submissions("320193", start=None)

    payload = asyncio.run(exercise())
    recent = payload["filings"]["recent"]  # type: ignore[index]

    assert recent["accessionNumber"] == ["a-1", "h-001", "h-002"]
    assert recent["form"] == ["10-K", "10-Q", "10-Q"]


def test_history_files_that_end_before_the_start_date_are_not_fetched(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("CIK0000320193.json"):
            return httpx.Response(
                200,
                json={
                    "filings": {
                        "recent": {"accessionNumber": ["a-1"]},
                        "files": [
                            {
                                "name": "CIK0000320193-submissions-001.json",
                                "filingTo": "2015-12-31",
                            },
                            {
                                "name": "CIK0000320193-submissions-002.json",
                                "filingTo": "2024-12-31",
                            },
                            # An unparseable bound is fetched rather than dropped.
                            {"name": "CIK0000320193-submissions-003.json", "filingTo": "soon"},
                        ],
                    }
                },
            )
        return httpx.Response(200, json={"accessionNumber": [request.url.path[-8:-5]]})

    transport = _transport(respond)

    async def exercise() -> None:
        from datetime import date

        async with _client(tmp_path, transport) as client:
            await client.complete_submissions("320193", start=date(2020, 1, 1))

    asyncio.run(exercise())
    fetched = [str(request.url).rsplit("/", 1)[-1] for request in transport.requested]  # type: ignore[attr-defined]

    assert "CIK0000320193-submissions-001.json" not in fetched
    assert "CIK0000320193-submissions-002.json" in fetched
    assert "CIK0000320193-submissions-003.json" in fetched


def test_a_filing_is_fetched_once_and_then_read_from_the_cache(tmp_path: Path) -> None:
    transport = _transport(text="<html><body>filing</body></html>")

    async def exercise() -> tuple[str, str]:
        async with _client(tmp_path, transport) as client:
            first = await client.filing_html("320193", "0000320193-24-000123", "aapl-10k.htm")
            second = await client.filing_html("320193", "0000320193-24-000123", "aapl-10k.htm")
            return first, second

    first, second = asyncio.run(exercise())
    cached = tmp_path / "cache" / "0000320193-24-000123-aapl-10k.htm.gz"

    assert first == second == "<html><body>filing</body></html>"
    assert len(transport.requested) == 1  # type: ignore[attr-defined]
    assert str(transport.requested[0].url) == (  # type: ignore[attr-defined]
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-10k.htm"
    )
    with gzip.open(cached, "rt", encoding="utf-8") as stream:
        assert stream.read() == first


def test_caching_can_be_turned_off_for_a_one_off_read(tmp_path: Path) -> None:
    transport = _transport(text="<html>filing</html>")

    async def exercise() -> None:
        async with _client(tmp_path, transport, cache_filings=False) as client:
            await client.filing_html("320193", "0000320193-24-000123", "aapl-10k.htm")
            await client.filing_html("320193", "0000320193-24-000123", "aapl-10k.htm")

    asyncio.run(exercise())

    assert len(transport.requested) == 2  # type: ignore[attr-defined]
    assert not list((tmp_path / "cache").glob("*.gz"))


def test_a_transient_server_error_is_retried_and_then_succeeds(tmp_path: Path) -> None:
    attempts = {"count": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    async def exercise() -> dict[str, object]:
        async with _client(tmp_path, _transport(respond)) as client:
            return await client.company_tickers_exchange()

    assert asyncio.run(exercise()) == {"ok": True}
    assert attempts["count"] == 3


def test_a_persistent_failure_is_raised_rather_than_retried_forever(tmp_path: Path) -> None:
    attempts = {"count": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(500)

    async def exercise() -> None:
        async with _client(tmp_path, _transport(respond)) as client:
            await client.submissions("320193")

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(exercise())
    assert attempts["count"] == 4
