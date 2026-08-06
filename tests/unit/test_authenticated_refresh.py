from datetime import date

import orjson
import pytest

from edgar_moe.data.refresh import (
    UniverseMember,
    collect_authenticated_data,
    load_universe_csv,
    refresh_authenticated_to_disk,
    verify_authenticated_bundle,
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

    async def corporate_actions(self, start, end, symbols=None) -> dict:
        return {
            "corporate_actions": {
                "cash_dividends": [
                    {"symbol": symbol, "ex_date": start.isoformat()} for symbol in (symbols or [])
                ]
            }
        }


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


class StreamingFakeSec(FakeSec):
    async def complete_submissions(self, cik: str, start=None) -> dict:
        return {
            "cik": cik,
            "name": "Example Inc.",
            "sic": "3571",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000000001-25-000001"],
                    "acceptanceDateTime": ["2025-05-01T17:30:00-04:00"],
                    "reportDate": ["2025-03-31"],
                    "form": ["10-Q"],
                    "primaryDocument": ["example.htm"],
                }
            },
        }

    async def filing_html(self, cik: str, accession: str, primary_document: str) -> str:
        return "<html><body>Item 1A. Risk Factors " + "risk " * 100 + " Item 1B.</body></html>"


async def test_streaming_refresh_downloads_filings_and_verifies_hashes(tmp_path) -> None:
    destination = await refresh_authenticated_to_disk(
        StreamingFakeSec(),
        FakeMarket(),
        FakeMacro(),
        [UniverseMember(cik="1", symbol="TEST")],
        output_root=tmp_path,
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
        as_of=date(2025, 12, 31),
        macro_series=["VIXCLS"],
    )

    manifest = verify_authenticated_bundle(destination)
    filing_index = orjson.loads((destination / "sec" / "filing-index.json").read_bytes())

    assert manifest.row_counts["filing_documents"] == 1
    assert manifest.row_counts["daily_bars"] == 2
    assert manifest.row_counts["corporate_actions"] == 2
    assert filing_index[0]["status"] == "ok"
    assert (destination / filing_index[0]["localPath"]).exists()


class EligibilitySec(StreamingFakeSec):
    async def complete_submissions(self, cik: str, start=None) -> dict:
        if cik.endswith("2"):
            return {
                "cik": cik,
                "name": "Foreign Example",
                "sic": "9999",
                "filings": {
                    "recent": {
                        "accessionNumber": ["0000000002-25-000001"],
                        "acceptanceDateTime": ["2025-05-01T17:30:00-04:00"],
                        "reportDate": ["2024-12-31"],
                        "form": ["20-F"],
                        "primaryDocument": ["foreign.htm"],
                    }
                },
            }
        return await super().complete_submissions(cik, start)


async def test_streaming_refresh_excludes_issuers_without_periodic_forms(tmp_path) -> None:
    destination = await refresh_authenticated_to_disk(
        EligibilitySec(),
        FakeMarket(),
        FakeMacro(),
        [
            UniverseMember(cik="1", symbol="TEST"),
            UniverseMember(cik="2", symbol="FOREIGN"),
        ],
        output_root=tmp_path,
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
        as_of=date(2025, 12, 31),
        macro_series=["VIXCLS"],
    )

    manifest = verify_authenticated_bundle(destination)
    universe = orjson.loads((destination / "universe.json").read_bytes())
    bar_symbols = {
        orjson.loads(line)["symbol"]
        for line in (destination / "market" / "daily-bars.ndjson").read_bytes().splitlines()
    }

    assert manifest.row_counts["requested_universe_members"] == 2
    assert manifest.row_counts["universe_members"] == 1
    assert manifest.row_counts["ineligible_form_issuers"] == 1
    assert [row["symbol"] for row in universe] == ["TEST"]
    assert bar_symbols == {"SPY", "TEST"}
