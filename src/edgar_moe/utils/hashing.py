"""Dependency-free SHA-256 content identities.

Kept free of the research stack so lightweight operator and forward modules can
hash evidence without importing duckdb, polars, or pandas.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_BYTES = 1024 * 1024


def hash_file(path: str | Path) -> tuple[str, int]:
    """Return a file's SHA-256 hex digest and byte count, streaming 1 MiB chunks."""
    digest = hashlib.sha256()
    size_bytes = 0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


def sha256_file(path: str | Path) -> str:
    """Return a file's SHA-256 hex digest."""
    return hash_file(path)[0]
