import gzip
import hashlib
from datetime import date
from pathlib import Path

import orjson
import pytest

from edgar_moe.data.refresh import (
    UniverseMember,
    _download_filing_record,
    collect_authenticated_data,
    load_universe_csv,
    refresh_authenticated_to_disk,
    verify_authenticated_bundle,
    write_authenticated_bundle,
)
from edgar_moe.forward.operations import seed_filing_documents


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


async def test_forward_cache_seeds_only_after_request_contract_and_avoids_refetch(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    cache = tmp_path / "cache"
    cached = cache / "CIK0000000001" / "0000000001-25-000001.html.gz"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(gzip.compress(b"<html>Invented cached filing</html>"))
    cached.with_name(cached.name + ".sha256").write_text(
        hashlib.sha256(cached.read_bytes()).hexdigest() + "\n", encoding="ascii"
    )
    contract = raw_root / "2025-12-31" / "request.json"
    seed_count = 0

    def seed() -> int:
        nonlocal seed_count
        assert orjson.loads(contract.read_bytes())["as_of"] == "2025-12-31"
        seed_count += 1
        return seed_filing_documents(raw_root=raw_root, filing_cache=cache, cutoff="2025-12-31")

    class NoFilingFetchSec(StreamingFakeSec):
        async def filing_html(self, cik: str, accession: str, primary_document: str) -> str:
            raise AssertionError("verified cached filing must not be fetched again")

    options = {
        "output_root": raw_root,
        "start": date(2025, 1, 1),
        "end": date(2025, 12, 31),
        "as_of": date(2025, 12, 31),
        "macro_series": ["VIXCLS"],
        "resume": True,
        "seed_filings": seed,
    }
    destination = await refresh_authenticated_to_disk(
        NoFilingFetchSec(),
        FakeMarket(),
        FakeMacro(),
        [UniverseMember(cik="1", symbol="TEST")],
        **options,
    )
    filing_index = orjson.loads((destination / "sec" / "filing-index.json").read_bytes())
    assert seed_count == 1
    assert filing_index[0]["status"] == "ok"
    assert (destination / filing_index[0]["localPath"]).read_bytes() == cached.read_bytes()
    verify_authenticated_bundle(destination)

    def fail_if_reseeded() -> int:
        raise AssertionError("a completed checkpoint must not be reseeded")

    await refresh_authenticated_to_disk(
        NoFilingFetchSec(),
        FakeMarket(),
        FakeMacro(),
        [UniverseMember(cik="1", symbol="TEST")],
        **{**options, "seed_filings": fail_if_reseeded},
    )


async def test_failed_seed_preserves_contract_for_same_request_resume(tmp_path: Path) -> None:
    members = [UniverseMember(cik="1", symbol="TEST")]
    options = {
        "output_root": tmp_path,
        "start": date(2025, 1, 1),
        "end": date(2025, 12, 31),
        "as_of": date(2025, 12, 31),
        "macro_series": ["VIXCLS"],
        "resume": True,
    }
    request_path = tmp_path / "2025-12-31" / "request.json"

    def interrupted_seed() -> int:
        assert request_path.is_file()
        raise RuntimeError("injected cache interruption")

    with pytest.raises(RuntimeError, match="injected cache interruption"):
        await refresh_authenticated_to_disk(
            StreamingFakeSec(),
            FakeMarket(),
            FakeMacro(),
            members,
            seed_filings=interrupted_seed,
            **options,
        )
    assert request_path.is_file()
    resumed = await refresh_authenticated_to_disk(
        StreamingFakeSec(), FakeMarket(), FakeMacro(), members, seed_filings=lambda: 0, **options
    )
    verify_authenticated_bundle(resumed)


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


async def test_completed_refresh_resume_is_immutable_and_rejects_changed_inputs(tmp_path) -> None:
    members = [UniverseMember(cik="1", symbol="TEST")]
    options = {
        "output_root": tmp_path,
        "start": date(2025, 1, 1),
        "end": date(2025, 12, 31),
        "as_of": date(2025, 12, 31),
        "macro_series": ["VIXCLS"],
    }
    destination = await refresh_authenticated_to_disk(
        StreamingFakeSec(), FakeMarket(), FakeMacro(), members, **options
    )
    manifest_path = destination / "manifest.json"
    original_manifest = manifest_path.read_bytes()

    class NoFetchSec(StreamingFakeSec):
        async def complete_submissions(self, cik: str, start=None) -> dict:
            raise AssertionError("completed checkpoint must not refetch SEC")

    reused = await refresh_authenticated_to_disk(
        NoFetchSec(), FakeMarket(), FakeMacro(), members, resume=True, **options
    )
    assert reused == destination
    assert manifest_path.read_bytes() == original_manifest
    with pytest.raises(ValueError, match="request differs"):
        await refresh_authenticated_to_disk(
            NoFetchSec(), FakeMarket(), FakeMacro(), members, resume=True, feed="sip", **options
        )
    assert manifest_path.read_bytes() == original_manifest

    bars = destination / "market" / "daily-bars.ndjson"
    bars.write_bytes(bars.read_bytes().splitlines(keepends=True)[0])
    with pytest.raises(ValueError, match="Hash mismatch"):
        await refresh_authenticated_to_disk(
            NoFetchSec(), FakeMarket(), FakeMacro(), members, resume=True, **options
        )
    assert manifest_path.read_bytes() == original_manifest


async def test_interrupted_refresh_refetches_tampered_bars_and_rejects_wrong_request(
    tmp_path,
) -> None:
    class FailAfterBarsMarket(FakeMarket):
        async def corporate_actions(self, start, end, symbols=None) -> dict:
            raise RuntimeError("injected outage after market bars")

    class CountingMarket(FakeMarket):
        def __init__(self) -> None:
            self.bars_calls = 0

        async def daily_bars(self, symbols, start, end, feed="iex") -> dict:
            self.bars_calls += 1
            return await super().daily_bars(symbols, start, end, feed=feed)

    class CountingSec(StreamingFakeSec):
        def __init__(self) -> None:
            self.submission_calls = 0
            self.fact_calls = 0
            self.html_calls = 0

        async def complete_submissions(self, cik: str, start=None) -> dict:
            self.submission_calls += 1
            return await super().complete_submissions(cik, start)

        async def company_facts(self, cik: str) -> dict:
            self.fact_calls += 1
            return await super().company_facts(cik)

        async def filing_html(self, cik: str, accession: str, primary_document: str) -> str:
            self.html_calls += 1
            return await super().filing_html(cik, accession, primary_document)

    members = [UniverseMember(cik="1", symbol="TEST")]
    options = {
        "output_root": tmp_path,
        "start": date(2025, 1, 1),
        "end": date(2025, 12, 31),
        "as_of": date(2025, 12, 31),
        "macro_series": ["VIXCLS"],
    }
    with pytest.raises(RuntimeError, match="injected outage"):
        await refresh_authenticated_to_disk(
            StreamingFakeSec(), FailAfterBarsMarket(), FakeMacro(), members, **options
        )
    destination = tmp_path / "2025-12-31"
    assert not (destination / "manifest.json").exists()
    bars = destination / "market" / "daily-bars.ndjson"
    assert len(bars.read_bytes().splitlines()) == 2
    bars.write_bytes(bars.read_bytes().splitlines(keepends=True)[0])
    facts = destination / "sec" / "CIK0000000001-companyfacts.json.gz"
    facts.write_bytes(facts.read_bytes() + b"damaged")
    filing = destination / "sec" / "filings" / "CIK0000000001" / "0000000001-25-000001.html.gz"
    filing.with_name(filing.name + ".sha256").unlink()

    with pytest.raises(FileExistsError, match="use resume=True"):
        await refresh_authenticated_to_disk(
            StreamingFakeSec(), FakeMarket(), FakeMacro(), members, **options
        )

    with pytest.raises(ValueError, match="original request"):
        await refresh_authenticated_to_disk(
            StreamingFakeSec(),
            FakeMarket(),
            FakeMacro(),
            members,
            resume=True,
            macro_series=["DGS10"],
            **{key: value for key, value in options.items() if key != "macro_series"},
        )
    market = CountingMarket()
    sec = CountingSec()
    resumed = await refresh_authenticated_to_disk(
        sec, market, FakeMacro(), members, resume=True, **options
    )
    assert resumed == destination
    assert market.bars_calls == 1
    assert sec.submission_calls == 0
    assert sec.fact_calls == 1
    assert sec.html_calls == 1
    assert verify_authenticated_bundle(resumed).row_counts["daily_bars"] == 2


async def test_completed_legacy_checkpoint_can_resume_but_unidentified_partial_cannot(
    tmp_path,
) -> None:
    members = [UniverseMember(cik="1", symbol="TEST")]
    options = {
        "output_root": tmp_path,
        "start": date(2025, 1, 1),
        "end": date(2025, 12, 31),
        "as_of": date(2025, 12, 31),
        "macro_series": ["VIXCLS"],
    }
    destination = await refresh_authenticated_to_disk(
        StreamingFakeSec(), FakeMarket(), FakeMacro(), members, **options
    )
    manifest_path = destination / "manifest.json"
    legacy = orjson.loads(manifest_path.read_bytes())
    legacy["hashes"].pop("request")
    legacy["configuration"]["assets"].pop("request")
    legacy["configuration"].pop("market_batch_size")
    manifest_path.write_bytes(orjson.dumps(legacy))
    (destination / "request.json").unlink()

    assert (
        await refresh_authenticated_to_disk(
            StreamingFakeSec(), FakeMarket(), FakeMacro(), members, resume=True, **options
        )
        == destination
    )
    assert not (destination / "request.json").exists()

    partial = tmp_path / "2025-12-30"
    partial.mkdir()
    (partial / "unidentified-file").write_bytes(b"old")
    with pytest.raises(ValueError, match="no request contract"):
        await refresh_authenticated_to_disk(
            StreamingFakeSec(),
            FakeMarket(),
            FakeMacro(),
            members,
            resume=True,
            as_of=date(2025, 12, 30),
            end=date(2025, 12, 30),
            **{key: value for key, value in options.items() if key not in {"as_of", "end"}},
        )


async def test_failed_filing_refetch_does_not_promote_stale_local_document(tmp_path) -> None:
    class FailedFilingSec(StreamingFakeSec):
        async def filing_html(self, cik: str, accession: str, primary_document: str) -> str:
            raise RuntimeError("injected SEC outage")

    issuer_directory = tmp_path / "sec" / "filings" / "CIK0000000001"
    issuer_directory.mkdir(parents=True)
    stale = issuer_directory / "0000000001-25-000001.html.gz"
    stale.write_bytes(b"stale incomplete document")
    result = await _download_filing_record(
        FailedFilingSec(),
        UniverseMember(cik="1", symbol="TEST"),
        {"accessionNumber": "0000000001-25-000001", "primaryDocument": "example.htm"},
        issuer_directory=issuer_directory,
        run_directory=tmp_path,
        resume=True,
    )

    assert result["status"] == "failed"
    assert result["localPath"] == ""
    assert "injected SEC outage" in result["error"]
    assert stale.read_bytes() == b"stale incomplete document"
