"""Benchmark the private local registry against deterministic synthetic records.

This measures direct ForwardRegistry + SQLite operations, not the public API,
hosted latency, edge-cache hit rate, Postgres query plans, throughput, or a
production SLO. No credentials, provider calls, or market data are used.
"""

from __future__ import annotations

import argparse
import math
import platform
import statistics
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import orjson

from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.domain import (
    DatasetRegistration,
    ForecastDraft,
    LabelDraft,
    ModelRegistration,
    RunRegistration,
)
from edgar_moe.forward.registry import ForwardRegistry
from edgar_moe.forward.uncertainty import _bootstrap

_MODEL = "synthetic-read-benchmark"
_DATASET = "synthetic-read-benchmark"
_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64


def _seed(registry: ForwardRegistry, *, rows: int, settled_percent: int) -> int:
    registry.register_dataset(
        DatasetRegistration(
            dataset_id=_DATASET,
            as_of=date(2025, 1, 1),
            source_manifest_hash=_HASH_A,
            manifest_sha256=_HASH_B,
            row_counts={"synthetic_forecasts": rows},
            provenance={"source": "deterministic synthetic benchmark"},
        )
    )
    registry.register_model(
        ModelRegistration(
            model_id=_MODEL,
            name="Synthetic read benchmark",
            version="benchmark-only",
            training_dataset_id=_DATASET,
            selection_hash=_HASH_A,
            artifact_uri="synthetic://no-model",
            artifact_sha256=_HASH_B,
            config_hash=_HASH_C,
            champion_family="synthetic",
            frozen_at=datetime(2024, 12, 31, tzinfo=UTC),
        )
    )
    per_month = rows // 12
    for month in range(1, 13):
        as_of = datetime(2025, month, 10, 12, tzinfo=UTC)
        run = registry.start_run(
            RunRegistration(
                run_type="forecast",
                as_of=as_of,
                code_revision="synthetic",
                config_hash=_HASH_C,
                dataset_id=_DATASET,
                model_id=_MODEL,
            ),
            run_id=f"synthetic-forecast-{month:02d}",
        )
        drafts = []
        for index in range(per_month):
            number = (month - 1) * per_month + index
            drafts.append(
                ForecastDraft(
                    event_id=f"synthetic-event-{number:05d}",
                    accession_number=f"0000000000-25-{number:06d}",
                    security_id=f"synthetic-asset-{number:05d}",
                    ticker=f"S{number:05d}",
                    company_name="Synthetic fixture (not a security)",
                    form="10-Q",
                    accepted_at=as_of - timedelta(hours=1),
                    entry_at=as_of + timedelta(hours=12),
                    entry_date=(as_of + timedelta(days=1)).date(),
                    horizon_at=as_of + timedelta(days=30),
                    industry_code="0000",
                    score=math.sin(number * 0.23),
                    rank=(index + 1) / per_month,
                    fundamental_score=None,
                    expert_weights={"synthetic": 1.0},
                )
            )
        if registry.append_forecasts(run.run_id, drafts)["inserted"] != per_month:
            raise RuntimeError("synthetic fixture did not insert all forecasts")
        registry.complete_run(run.run_id, result_counts={"forecasts": per_month})

    settled_per_month = per_month * settled_percent // 100
    settled = settled_per_month * 12
    if settled:
        settlement = registry.start_run(
            RunRegistration(
                run_type="settlement",
                as_of=datetime(2026, 2, 1, tzinfo=UTC),
                code_revision="synthetic",
                config_hash=_HASH_C,
                dataset_id=_DATASET,
                model_id=_MODEL,
            ),
            run_id="synthetic-settlement",
        )
        labels = []
        for offset in range(0, rows, 100):
            for item in registry.list_forecasts(limit=min(100, rows - offset), offset=offset)[
                "items"
            ]:
                number = int(item["event_id"].rsplit("-", 1)[1])
                if number % per_month >= settled_per_month:
                    continue
                labels.append(
                    LabelDraft(
                        forecast_id=item["forecast_id"],
                        source_dataset_id=_DATASET,
                        realized_abnormal_return=math.cos(number * 0.17),
                        matured_at=datetime(2026, 1, 31, tzinfo=UTC),
                    )
                )
        if registry.settle_labels(settlement.run_id, labels)["inserted"] != settled:
            raise RuntimeError("synthetic fixture did not settle requested labels")
        registry.complete_run(settlement.run_id, result_counts={"labels": settled})
    return settled


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _measure(
    operation: Callable[[], object], *, samples: int, cold_interval: bool = False
) -> dict[str, int | float]:
    durations: list[float] = []
    errors = 0
    for _ in range(samples):
        if cold_interval:
            _bootstrap.cache_clear()
        start = time.perf_counter()
        try:
            operation()
        except Exception:
            errors += 1
        durations.append((time.perf_counter() - start) * 1000)
    return {
        "requests": samples,
        "errors": errors,
        "p50_ms": round(statistics.median(durations), 3),
        "p95_ms": round(_percentile(durations, 0.95), 3),
        "p99_ms": round(_percentile(durations, 0.99), 3),
        "max_ms": round(max(durations), 3),
    }


def measure(*, rows: int = 240, settled_percent: int = 75, samples: int = 20) -> dict[str, object]:
    if rows < 120 or rows > 5000 or rows % 12:
        raise ValueError("rows must be a multiple of 12 between 120 and 5000")
    if not 0 <= settled_percent <= 100:
        raise ValueError("settled_percent must be 0..100")
    if not 5 <= samples <= 100:
        raise ValueError("samples must be 5..100")
    with tempfile.TemporaryDirectory(prefix="edgar-moe-read-benchmark-") as directory:
        database = RegistryDatabase(f"sqlite:///{Path(directory) / 'registry.sqlite3'}")
        try:
            database.create_schema()
            registry = ForwardRegistry(database, actor="synthetic-benchmark")
            settled = _seed(registry, rows=rows, settled_percent=settled_percent)
            operations: dict[str, Callable[[], object]] = {
                "status_uncached": registry.status,
                "forecasts_page": lambda: registry.list_forecasts(limit=50),
                "forecasts_filtered": lambda: registry.list_forecasts(limit=50, ticker="S00000"),
                "performance_warm": registry.performance,
            }
            # Warm each SQL path; the interval's bounded process cache is
            # explicitly cold only in the separate probe.
            for operation in operations.values():
                operation()
            results = {
                name: _measure(operation, samples=samples) for name, operation in operations.items()
            }
            results["performance_cold_interval"] = _measure(
                registry.performance, samples=5, cold_interval=True
            )
            return {
                "schema_version": 1,
                "scope": "synthetic_private_registry_sqlite_sequential",
                "python": platform.python_version(),
                "platform": platform.platform(),
                "forecasts": rows,
                "settled": settled,
                "calendar_months": 12 if settled >= 12 else settled,
                "samples_per_warm_route": samples,
                "results": results,
                "caveats": [
                    "No network, serverless cold start, edge cache, or Postgres is measured.",
                    "Sequential requests cannot establish concurrency capacity or an SLO.",
                    "Synthetic scores and labels are not investment performance evidence.",
                ],
            }
        finally:
            _bootstrap.cache_clear()
            database.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=240)
    parser.add_argument("--settled-percent", type=int, default=75)
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()
    print(orjson.dumps(measure(**vars(args)), option=orjson.OPT_SORT_KEYS).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
