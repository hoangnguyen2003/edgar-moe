"""Measure the local cost of verifying a synthetic cached market-bars file.

This is an I/O microbenchmark, not a provider throughput or production SLA.
It creates no market data and makes no network calls.
"""

from __future__ import annotations

import argparse
import statistics
import tempfile
import time
from pathlib import Path

import orjson

from edgar_moe.data.refresh import (
    _count_nonempty_lines,
    _has_verified_sidecar,
    _write_sidecar_hash,
)


def measure(size_mib: int = 16, samples: int = 5) -> dict[str, float | int | str]:
    if not 1 <= size_mib <= 256 or not 1 <= samples <= 20:
        raise ValueError("size_mib must be 1..256 and samples must be 1..20")
    line = b'{"symbol":"TEST","t":"2025-01-01","c":100.0}\n'
    chunk = line * 4096
    with tempfile.TemporaryDirectory(prefix="edgar-moe-resume-benchmark-") as directory:
        bars = Path(directory) / "daily-bars.ndjson"
        with bars.open("wb") as stream:
            while stream.tell() < size_mib * 1024 * 1024:
                stream.write(chunk)
        _write_sidecar_hash(bars)
        legacy: list[float] = []
        verified: list[float] = []
        for _ in range(samples):
            start = time.perf_counter()
            count = _count_nonempty_lines(bars)
            legacy.append(time.perf_counter() - start)
            start = time.perf_counter()
            if not _has_verified_sidecar(bars):
                raise RuntimeError("synthetic sidecar failed integrity verification")
            if _count_nonempty_lines(bars) != count:
                raise RuntimeError("synthetic line count changed")
            verified.append(time.perf_counter() - start)
        return {
            "schema_version": 1,
            "scope": "synthetic_local_cached_bars_only",
            "bytes": bars.stat().st_size,
            "rows": count,
            "samples": samples,
            "legacy_count_median_ms": round(statistics.median(legacy) * 1000, 3),
            "verified_count_median_ms": round(statistics.median(verified) * 1000, 3),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size-mib", type=int, default=16)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    print(orjson.dumps(measure(args.size_mib, args.samples), option=orjson.OPT_SORT_KEYS).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
