"""Capture bounded, redacted plans for the hosted forward read path.

Run manually with a verified SELECT-only database role. No query results, SQL
parameters, raw plan JSON, or connection strings are written to the report.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from scripts.verify_postgres_reader import ReaderRoleAuditError, audit_reader_role

_ARTIFACT_ROOT = Path(__file__).resolve().parents[1] / "data" / "artifacts"
_NODE_TYPE = re.compile(r"[A-Za-z ]{1,64}\Z")
_METRICS = (
    "Plan Rows",
    "Actual Rows",
    "Actual Loops",
    "Startup Cost",
    "Total Cost",
    "Actual Startup Time",
    "Actual Total Time",
    "Shared Hit Blocks",
    "Shared Read Blocks",
    "Shared Dirtied Blocks",
    "Shared Written Blocks",
    "Temp Read Blocks",
    "Temp Written Blocks",
)

# These are the actual read shapes in ForwardRegistry.status(),
# list_forecasts(), and performance(). Keep them fixed and review any change.
_QUERIES = (
    (
        "status_forecast_count",
        "SELECT count(forecast_id) FROM public.forward_forecasts",
        (),
    ),
    (
        "status_latest_success",
        "SELECT * FROM public.forward_runs WHERE status = 'succeeded' "
        "ORDER BY finished_at DESC LIMIT 1",
        (),
    ),
    (
        "status_latest_run",
        "SELECT * FROM public.forward_runs ORDER BY started_at DESC, run_id DESC LIMIT 1",
        (),
    ),
    (
        "forecast_page",
        "SELECT f.*, l.* FROM public.forward_forecasts AS f "
        "LEFT JOIN public.forward_labels AS l ON l.forecast_id = f.forecast_id "
        "ORDER BY f.forecast_as_of DESC, f.ticker LIMIT 25 OFFSET 0",
        (),
    ),
    (
        "performance_pairs",
        "SELECT f.score, l.realized_abnormal_return, f.accepted_at "
        "FROM public.forward_forecasts AS f "
        "JOIN public.forward_labels AS l ON l.forecast_id = f.forecast_id "
        "ORDER BY f.accepted_at, f.forecast_id",
        (),
    ),
)
_FILTER_QUERY = (
    "SELECT f.*, l.* FROM public.forward_forecasts AS f "
    "LEFT JOIN public.forward_labels AS l ON l.forecast_id = f.forecast_id "
    "WHERE f.ticker = %s "
    "ORDER BY f.forecast_as_of DESC, f.ticker LIMIT 25 OFFSET 0"
)


class PlanCaptureError(RuntimeError):
    """A capture or safety contract failed; never include driver details."""


def _safe_node(value: Any, *, depth: int = 0) -> dict[str, Any]:
    if not isinstance(value, dict) or depth > 16:
        raise PlanCaptureError("Postgres returned an invalid or excessive plan tree")
    node_type = value.get("Node Type")
    if not isinstance(node_type, str) or not _NODE_TYPE.fullmatch(node_type):
        raise PlanCaptureError("Postgres returned an invalid plan node type")
    safe: dict[str, Any] = {"node_type": node_type}
    for key in _METRICS:
        number = value.get(key)
        if number is not None:
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise PlanCaptureError("Postgres returned a nonnumeric plan metric")
            if not math.isfinite(number):
                raise PlanCaptureError("Postgres returned a nonfinite plan metric")
            safe[key.lower().replace(" ", "_")] = number
    children = value.get("Plans", [])
    if not isinstance(children, list) or len(children) > 32:
        raise PlanCaptureError("Postgres returned an invalid or excessive plan tree")
    if children:
        safe["children"] = [_safe_node(child, depth=depth + 1) for child in children]
    return safe


def _safe_plan(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise PlanCaptureError("Postgres returned an invalid EXPLAIN result")
    entry = raw[0]
    plan = _safe_node(entry.get("Plan"))
    for original, safe_key in (
        ("Planning Time", "planning_ms"),
        ("Execution Time", "execution_ms"),
    ):
        value = entry.get(original)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PlanCaptureError("Postgres returned an invalid plan timing")
        if not math.isfinite(value):
            raise PlanCaptureError("Postgres returned a nonfinite plan timing")
        plan[safe_key] = value
    return plan


def _explain(
    connection: psycopg.Connection[Any], query: str, params: tuple[Any, ...]
) -> dict[str, Any]:
    row = connection.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query, params).fetchone()
    if row is None:
        raise PlanCaptureError("Postgres returned no EXPLAIN result")
    return _safe_plan(row[0])


def _count_rows(connection: psycopg.Connection[Any], table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM public.{table}").fetchone()
    if row is None:
        raise PlanCaptureError("Postgres returned no table count")
    return int(row[0])


def capture(database_url: str) -> dict[str, Any]:
    if not database_url.strip():
        raise PlanCaptureError("EDGAR_MOE_REGISTRY_READ_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise PlanCaptureError("A direct PostgreSQL reader URL is required")
    # The existing audit rejects elevated roles and proves all writes and DDL
    # denied before any EXPLAIN ANALYZE runs. It never logs the URL.
    audit_reader_role(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET LOCAL transaction_read_only = on")
        connection.execute("SET LOCAL statement_timeout = '3000ms'")
        connection.execute("SET LOCAL lock_timeout = '500ms'")
        counts = {
            table: _count_rows(connection, table)
            for table in ("forward_runs", "forward_forecasts", "forward_labels")
        }
        # Select a real, high-frequency filter value, but never emit or retain it.
        ticker_row = connection.execute(
            "SELECT ticker FROM public.forward_forecasts "
            "GROUP BY ticker ORDER BY count(*) DESC, ticker LIMIT 1"
        ).fetchone()
        plans = {name: _explain(connection, query, params) for name, query, params in _QUERIES}
        if ticker_row is not None:
            plans["forecast_ticker_filter"] = _explain(connection, _FILTER_QUERY, (ticker_row[0],))
        connection.rollback()
    return {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "source": "verified_select_only_postgres_reader",
        "transaction": "read_only_3s_statement_timeout_500ms_lock_timeout",
        "table_counts": counts,
        "filter_selection": "highest_frequency_ticker_not_retained"
        if ticker_row
        else "no_forecasts",
        "plans": plans,
        "limitations": [
            "EXPLAIN timings are provider-side samples, not end-to-end API latency",
            "one capture does not establish capacity, cold-start attribution, or an SLO",
        ],
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    destination = path.resolve()
    if not destination.is_relative_to(_ARTIFACT_ROOT.resolve()):
        raise PlanCaptureError("Output must be under ignored data/artifacts/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        # Resolve the output boundary before touching the provider.
        if not args.output.resolve().is_relative_to(_ARTIFACT_ROOT.resolve()):
            raise PlanCaptureError("Output must be under ignored data/artifacts/")
        report = capture(os.environ.get("EDGAR_MOE_REGISTRY_READ_DATABASE_URL", ""))
        _write_report(args.output, report)
    except (PlanCaptureError, ReaderRoleAuditError, psycopg.Error, OSError) as error:
        # Driver and OS details can embed credentials or provider endpoints.
        print(f"Plan capture failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(f"Redacted plan report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
