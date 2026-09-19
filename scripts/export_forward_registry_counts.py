"""Export deterministic counts for every forward-registry table."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import func, select

from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.models import Base
from edgar_moe.forward.restore_rehearsal import REGISTRY_TABLES


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("EDGAR_MOE_REGISTRY_DATABASE_URL", ""),
        help="Registry URL; defaults to EDGAR_MOE_REGISTRY_DATABASE_URL.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def export_counts(database_url: str) -> dict[str, int]:
    if not database_url.strip():
        raise ValueError("A registry database URL is required")
    database = RegistryDatabase(database_url)
    try:
        with database.engine.connect() as connection, connection.begin():
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            counts: dict[str, int] = {}
            for table_name in REGISTRY_TABLES:
                table = Base.metadata.tables.get(table_name)
                if table is None:  # pragma: no cover - contract drift guard
                    raise RuntimeError(f"Unknown registry table: {table_name}")
                counts[table_name] = int(
                    connection.scalar(select(func.count()).select_from(table)) or 0
                )
            return counts
    finally:
        database.dispose()


def _write_new(path: Path, payload: dict[str, int]) -> None:
    output = path.expanduser().resolve()
    if output.exists():
        raise ValueError(f"Refusing to overwrite count report: {output}")
    if not output.parent.is_dir():
        raise ValueError(f"Count report parent directory does not exist: {output.parent}")
    with output.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> int:
    args = _parse_args()
    counts = export_counts(args.database_url)
    _write_new(args.output, counts)
    print(json.dumps(counts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
