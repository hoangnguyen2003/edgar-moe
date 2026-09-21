from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from edgar_moe.data.storage import sha256_file as storage_sha256_file
from edgar_moe.utils.hashing import hash_file, sha256_file
from edgar_moe.utils.timestamps import (
    NaiveTimestampError,
    TimestampFormatError,
    parse_aware_timestamp,
)


def test_timestamps_are_normalized_to_utc() -> None:
    expected = datetime(2026, 9, 21, 7, 17, tzinfo=UTC)
    assert parse_aware_timestamp("2026-09-21T07:17:00Z") == expected
    assert parse_aware_timestamp("2026-09-21T03:17:00-04:00") == expected
    assert parse_aware_timestamp("2026-09-21T03:17:00-04:00").tzinfo == UTC


@pytest.mark.parametrize("value", ["2026-09-21T07:17:00", "2026-09-21"])
def test_naive_timestamps_are_rejected(value: str) -> None:
    with pytest.raises(NaiveTimestampError, match="must include a timezone"):
        parse_aware_timestamp(value)


@pytest.mark.parametrize("value", [None, "", "   ", "yesterday", 1_726_902_000])
def test_non_iso_values_are_rejected(value: object) -> None:
    with pytest.raises(TimestampFormatError, match="must be an ISO-8601 timestamp"):
        parse_aware_timestamp(value)


def test_file_hashes_stream_digest_and_size(tmp_path: Path) -> None:
    content = b"x" * (3 * 1024 * 1024 + 7)  # spans several 1 MiB chunks
    path = tmp_path / "evidence.bin"
    path.write_bytes(content)

    digest = hashlib.sha256(content).hexdigest()
    assert hash_file(path) == (digest, len(content))
    assert sha256_file(path) == digest
    # data.storage keeps re-exporting the shared implementation for callers.
    assert storage_sha256_file is sha256_file
