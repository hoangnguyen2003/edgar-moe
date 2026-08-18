from datetime import date
from pathlib import Path

import numpy as np
import orjson
import pandas as pd
import pandas_market_calendars as mcal

from edgar_moe.data.refresh import UniverseMember, refresh_authenticated_to_disk
from edgar_moe.features.dataset import ResearchDataset, build_research_dataset
from edgar_moe.features.text import HashingTextEmbedder
from edgar_moe.settings import ResearchConfig

ACCESSIONS = [
    "0000000001-21-000001",
    "0000000001-23-000001",
    "0000000001-25-000001",
]
ACCEPTANCES = [
    "2021-06-01T17:30:00-04:00",
    "2023-06-01T17:30:00-04:00",
    "2025-06-02T17:30:00-04:00",
]
REPORT_DATES = ["2021-03-31", "2023-03-31", "2025-03-31"]


class PipelineSec:
    async def submissions(self, cik: str) -> dict:
        return await self.complete_submissions(cik)

    async def complete_submissions(self, cik: str, start=None) -> dict:
        return {
            "cik": cik,
            "name": "Test Company",
            "sic": "3571",
            "filings": {
                "recent": {
                    "accessionNumber": ACCESSIONS,
                    "acceptanceDateTime": ACCEPTANCES,
                    "reportDate": REPORT_DATES,
                    "form": ["10-Q"] * 3,
                    "primaryDocument": ["test.htm"] * 3,
                }
            },
        }

    async def company_facts(self, cik: str) -> dict:
        values = {
            "Assets": [100.0, 120.0, 150.0],
            "Liabilities": [40.0, 45.0, 50.0],
            "StockholdersEquity": [60.0, 75.0, 100.0],
            "Revenues": [50.0, 70.0, 90.0],
            "NetIncomeLoss": [5.0, 8.0, 12.0],
            "OperatingIncomeLoss": [7.0, 11.0, 16.0],
            "CashAndCashEquivalentsAtCarryingValue": [15.0, 18.0, 22.0],
            "AssetsCurrent": [45.0, 52.0, 63.0],
            "LiabilitiesCurrent": [20.0, 22.0, 25.0],
            "NetCashProvidedByUsedInOperatingActivities": [6.0, 10.0, 14.0],
        }
        return {
            "cik": cik,
            "facts": {
                "us-gaap": {
                    concept: {
                        "units": {
                            "USD": [
                                {
                                    "val": value,
                                    "end": report_date,
                                    "filed": acceptance[:10],
                                    "accn": accession,
                                    "form": "10-Q",
                                }
                                for value, report_date, acceptance, accession in zip(
                                    concept_values,
                                    REPORT_DATES,
                                    ACCEPTANCES,
                                    ACCESSIONS,
                                    strict=True,
                                )
                            ]
                        }
                    }
                    for concept, concept_values in values.items()
                }
            },
        }

    async def filing_html(self, cik: str, accession: str, primary_document: str) -> str:
        return Path("tests/fixtures/filing.html").read_text(encoding="utf-8")


class PipelineMarket:
    def __init__(self) -> None:
        sessions = mcal.get_calendar("NYSE").schedule(
            start_date="2020-01-01", end_date="2026-07-31"
        )
        self.rows: dict[str, list[dict]] = {"TEST": [], "SPY": []}
        for index, session in enumerate(sessions.index):
            day = pd.Timestamp(session).date().isoformat()
            benchmark_return = 0.002 * np.sin(index / 7)
            test_return = 1.3 * benchmark_return + 0.001 * np.cos(index / 11)
            for symbol, daily_return, base in (
                ("SPY", benchmark_return, 300.0),
                ("TEST", test_return, 80.0),
            ):
                open_price = base * (1 + 0.0002 * index)
                self.rows[symbol].append(
                    {
                        "t": f"{day}T04:00:00Z",
                        "o": open_price,
                        "h": open_price * (1 + abs(daily_return) + 0.001),
                        "l": open_price * (1 - abs(daily_return) - 0.001),
                        "c": open_price * (1 + daily_return),
                        "v": 1_000_000 + index * 100,
                        "n": 10_000,
                        "vw": open_price * (1 + daily_return / 2),
                    }
                )

    async def daily_bars(self, symbols, start, end, feed="iex") -> dict:
        return {symbol: self.rows[symbol] for symbol in symbols}

    async def corporate_actions(self, start, end, symbols=None) -> dict:
        return {"corporate_actions": []}


class PipelineMacro:
    async def observations_as_known(
        self, series_id, observation_start, observation_end, as_of
    ) -> list[dict]:
        return [
            {
                "date": f"{year}-01-01",
                "realtime_start": f"{year}-01-02",
                "value": str(index + 1.0),
            }
            for index, year in enumerate(range(2020, 2027))
        ]


async def test_authenticated_checkpoint_builds_point_in_time_dataset(tmp_path) -> None:
    checkpoint = await refresh_authenticated_to_disk(
        PipelineSec(),
        PipelineMarket(),
        PipelineMacro(),
        [
            UniverseMember(
                cik="1",
                symbol="TEST",
                security_id="asset-test",
                company_name="Test Company",
            )
        ],
        output_root=tmp_path / "raw",
        start=date(2020, 1, 1),
        end=date(2026, 7, 31),
        as_of=date(2026, 7, 31),
        macro_series=["DFF", "VIXCLS"],
    )
    config = ResearchConfig()
    config.data.universe_size = 10
    config.data.minimum_history_sessions = 20
    config.features.momentum_windows = [5, 21]
    config.features.volatility_windows = [21]
    config.features.beta_window = 63
    config.evaluation.horizon_sessions = 5

    progress: list[str] = []
    dataset = build_research_dataset(
        checkpoint,
        config=config,
        embedder=HashingTextEmbedder(dimensions=16),
        embedding_cache=tmp_path / "embedding-cache",
        progress=progress.append,
    )
    saved = dataset.save(tmp_path / "processed")
    restored = ResearchDataset.load(saved)

    assert len(dataset.events) == 3
    assert np.isfinite(dataset.target).all()
    assert dataset.modalities["text"].shape == (3, 24)
    assert not dataset.availability.empty
    assert dataset.attrition["included_events"] == 3
    assert restored.dataset_id == dataset.dataset_id
    assert restored.provenance["text_encoder"].startswith("hashing-blake2b-v1")
    assert np.allclose(restored.target, dataset.target)
    assert progress[0].startswith("Dataset filings 0/3")
    assert progress[-1].endswith("text cache hits 2; encoded 1")

    cached_progress: list[str] = []
    build_research_dataset(
        checkpoint,
        config=config,
        embedder=HashingTextEmbedder(dimensions=16),
        embedding_cache=tmp_path / "embedding-cache",
        progress=cached_progress.append,
    )
    assert cached_progress[-1].endswith("text cache hits 3; encoded 0")

    # A refreshed checkpoint can retain the same date-scoped source ID while
    # changing its verified manifest. The processed dataset identity must
    # follow that manifest so the append-only forward registry never receives
    # a conflicting payload under the old ID.
    manifest_path = checkpoint / "manifest.json"
    manifest = orjson.loads(manifest_path.read_bytes())
    manifest["created_at"] = "2026-08-01T00:00:00Z"
    manifest_path.write_bytes(orjson.dumps(manifest))
    refreshed = build_research_dataset(
        checkpoint,
        config=config,
        embedder=HashingTextEmbedder(dimensions=16),
        embedding_cache=tmp_path / "embedding-cache",
    )
    assert refreshed.source_manifest_hash != dataset.source_manifest_hash
    assert refreshed.dataset_id != dataset.dataset_id
