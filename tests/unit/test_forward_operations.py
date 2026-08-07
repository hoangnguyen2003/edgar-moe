from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from edgar_moe.forward.operations import (
    find_processed_dataset,
    seed_filing_documents,
    source_cutoff,
    update_filing_cache,
    validate_cutoff,
)


def test_source_cutoff_uses_new_york_date() -> None:
    clock = datetime(2026, 8, 7, 1, 30, tzinfo=UTC)
    assert source_cutoff(clock) == "2026-08-06"
    assert validate_cutoff("2026-08-06", now=clock) == "2026-08-06"
    with pytest.raises(ValueError, match="after the source-system date"):
        validate_cutoff("2026-08-07", now=clock)


def test_filing_cache_seeds_and_updates_without_overwrite(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    previous = raw_root / "2026-08-05"
    (previous / "manifest.json").parent.mkdir(parents=True)
    (previous / "manifest.json").write_text("{}", encoding="utf-8")
    previous_filing = previous / "sec" / "filings" / "CIK1" / "old.html.gz"
    previous_filing.parent.mkdir(parents=True)
    previous_filing.write_bytes(b"old")
    filing_cache = tmp_path / "cache"
    cached_filing = filing_cache / "CIK2" / "cached.html.gz"
    cached_filing.parent.mkdir(parents=True)
    cached_filing.write_bytes(b"cached")

    seeded = seed_filing_documents(
        raw_root=raw_root,
        filing_cache=filing_cache,
        cutoff="2026-08-06",
    )

    checkpoint = raw_root / "2026-08-06"
    assert seeded == 2
    assert (checkpoint / "sec" / "filings" / "CIK1" / "old.html.gz").read_bytes() == b"old"
    new_filing = checkpoint / "sec" / "filings" / "CIK3" / "new.html.gz"
    new_filing.parent.mkdir(parents=True)
    new_filing.write_bytes(b"new")
    assert update_filing_cache(checkpoint=checkpoint, filing_cache=filing_cache) == 2
    assert (filing_cache / "CIK3" / "new.html.gz").read_bytes() == b"new"


def test_find_processed_dataset_matches_cutoff_and_source_manifest(tmp_path: Path) -> None:
    checkpoint_manifest = tmp_path / "checkpoint" / "manifest.json"
    checkpoint_manifest.parent.mkdir()
    checkpoint_manifest.write_bytes(b'{"dataset":"source"}')
    source_hash = hashlib.sha256(checkpoint_manifest.read_bytes()).hexdigest()
    dataset = tmp_path / "processed" / "research-current"
    dataset.mkdir(parents=True)
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "as_of": "2026-08-06",
                "source_manifest_hash": source_hash,
            }
        ),
        encoding="utf-8",
    )

    assert (
        find_processed_dataset(
            processed_root=tmp_path / "processed",
            checkpoint_manifest=checkpoint_manifest,
            cutoff="2026-08-06",
        )
        == dataset
    )
