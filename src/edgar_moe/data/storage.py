from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

# Re-exported: callers import sha256_file from here.
from edgar_moe.utils.hashing import sha256_file as sha256_file


class ResearchStore:
    """Local analytical store backed by Parquet and DuckDB metadata."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.interim_dir = self.root / "interim"
        self.processed_dir = self.root / "processed"
        self.artifact_dir = self.root / "artifacts"
        self.catalog_path = self.root / "catalog.duckdb"
        for directory in (
            self.raw_dir,
            self.interim_dir,
            self.processed_dir,
            self.artifact_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._initialize_catalog()

    def _initialize_catalog(self) -> None:
        with duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dataset_assets (
                    asset_name VARCHAR PRIMARY KEY,
                    relative_path VARCHAR NOT NULL,
                    sha256 VARCHAR NOT NULL,
                    row_count BIGINT NOT NULL,
                    created_at TIMESTAMP DEFAULT current_timestamp
                )
                """
            )

    def write_parquet(self, asset_name: str, frame: pl.DataFrame) -> Path:
        path = self.processed_dir / f"{asset_name}.parquet"
        frame.write_parquet(path, compression="zstd", statistics=True)
        digest = sha256_file(path)
        with duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO dataset_assets
                    (asset_name, relative_path, sha256, row_count, created_at)
                VALUES (?, ?, ?, ?, current_timestamp)
                """,
                [asset_name, str(path.relative_to(self.root)), digest, frame.height],
            )
        return path

    def query(self, sql: str, parameters: Iterable[Any] | None = None) -> pl.DataFrame:
        with duckdb.connect(str(self.catalog_path), read_only=True) as connection:
            relation = connection.execute(sql, list(parameters or []))
            columns = [column[0] for column in relation.description]
            return pl.DataFrame(relation.fetchall(), schema=columns, orient="row")

    def catalog(self) -> list[dict[str, Any]]:
        if not self.catalog_path.exists():
            return []
        with duckdb.connect(str(self.catalog_path), read_only=True) as connection:
            rows = connection.execute(
                "SELECT asset_name, relative_path, sha256, row_count, created_at "
                "FROM dataset_assets ORDER BY asset_name"
            ).fetchall()
        return [
            {
                "asset_name": row[0],
                "relative_path": row[1],
                "sha256": row[2],
                "row_count": row[3],
                "created_at": row[4].isoformat(),
            }
            for row in rows
        ]


def stable_json_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()
