from datetime import date

import orjson
import pytest

from edgar_moe.data.refresh import (
    UniverseMember,
    collect_authenticated_data,
    load_universe_csv,
    write_authenticated_bundle,
)


class FakeSec:
    async def submissions(self, cik: str) -> dict:
        return {"cik": cik, "filings": {"recent": {"accessionNumber": ["one", "two"]}}}

    async def company_facts(self, cik: str) -> dict:
        return {"cik": cik, "facts": {"us-gaap": {"Assets": {}, "Revenues": {}}}}


class FakeMarket:
    async def daily_bars(self, symbols, start, end, feed="iex") -> dict:
        return {symbol: [{"t": start.isoformat(), "c": 100.0}] for symbol in symbols}


class FakeMacro:
    async def observations_as_known(self, series_id, observation_start, observation_end, as_of):
        return [{"date": observation_start, "value": "1.0", "realtime_start": as_of}]


def test_load_universe_normalizes_and_rejects_duplicates(tmp_path) -> None:
    path = tmp_path / "universe.csv"
    path.write_text("cik,symbol\n320193,aapl\n789019,msft\n", encoding="utf-8")
    members = load_universe_csv(path)
    assert members[0].cik == "0000320193"
    assert members[0].symbol == "AAPL"

    path.write_text("cik,symbol\n320193,aapl\n320193,msft\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_universe_csv(path)


async def test_authenticated_refresh_writes_hashed_manifest(tmp_path) -> None:
    members = [UniverseMember(cik="320193", symbol="aapl")]
    bundle = await collect_authenticated_data(
        FakeSec(),
        FakeMarket(),
        FakeMacro(),
        members,
        start=date(2025, 1, 1),
        end=date(2026, 7, 31),
        as_of=date(2026, 7, 31),
        macro_series=["VIXCLS", "DGS10"],
    )
    destination = write_authenticated_bundle(bundle, tmp_path)
    manifest = orjson.loads((destination / "manifest.json").read_bytes())

    assert bundle.bars.keys() == {"AAPL", "SPY"}
    assert manifest["dataset_id"] == "authenticated-2026-07-31"
    assert manifest["row_counts"]["recent_filing_records"] == 2
    assert manifest["row_counts"]["daily_bars"] == 2
    assert "bars" in manifest["hashes"]
    assert not any("key" in key.lower() or "secret" in key.lower() for key in manifest)


async def test_authenticated_refresh_rejects_future_end_date() -> None:
    with pytest.raises(ValueError, match="start <= end <= as_of"):
        await collect_authenticated_data(
            FakeSec(),
            FakeMarket(),
            FakeMacro(),
            [UniverseMember(cik="320193", symbol="AAPL")],
            start=date(2025, 1, 1),
            end=date(2026, 8, 1),
            as_of=date(2026, 7, 31),
            macro_series=["VIXCLS"],
        )
