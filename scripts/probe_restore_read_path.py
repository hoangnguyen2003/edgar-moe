"""Verify restored aggregate reads without mistaking forward freshness for recovery.

The independent workflow permission and full-count/evidence comparison gates are
still mandatory. This probe only establishes bounded aggregate-read availability
and consistency, not healthy forecasts, source-use approval or provider RPO/RTO.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.registry import ForwardRegistry
from edgar_moe.forward.restore_rehearsal import REGISTRY_TABLES


class RestoreReadPathError(ValueError):
    """Fixed redacted refusal code, never a row payload or driver message."""


def parse_expected_counts(payload: Any) -> dict[str, int]:
    if (
        not isinstance(payload, dict)
        or set(payload) != set(REGISTRY_TABLES)
        or any(type(value) is not int or value < 0 for value in payload.values())
    ):
        raise RestoreReadPathError("invalid_expected_registry_counts")
    return {str(key): int(value) for key, value in payload.items()}


def probe_registry(registry: ForwardRegistry, expected: dict[str, int]) -> dict[str, Any]:
    status = registry.status()
    forecasts = registry.list_forecasts(limit=1)
    performance = registry.performance()
    if any(not isinstance(payload, dict) for payload in (status, forecasts, performance)):
        raise RestoreReadPathError("invalid_read_response")
    health = status.get("health_status")
    if (
        status.get("configured") is not True
        or not isinstance(health, str)
        or health not in {"ok", "warning", "degraded"}
    ):
        raise RestoreReadPathError("invalid_registry_status")
    mappings = (
        (status, "model_count", "forward_models"),
        (status, "run_count", "forward_runs"),
        (status, "forecast_count", "forward_forecasts"),
        (status, "matured_count", "forward_labels"),
        (forecasts, "total", "forward_forecasts"),
        (performance, "forecast_count", "forward_forecasts"),
        (performance, "matured_count", "forward_labels"),
    )
    for payload, field, table in mappings:
        count = payload.get(field)
        if type(count) is not int or count != expected[table]:
            raise RestoreReadPathError("aggregate_count_mismatch")
    items = forecasts.get("items")
    if not isinstance(items, list) or len(items) != min(1, expected["forward_forecasts"]):
        raise RestoreReadPathError("forecast_sample_mismatch")
    # Select a fixed output schema. Never serialize status messages, forecast
    # samples, IDs, model identities, scores, returns or database errors.
    return {
        "status": "passed",
        "read_path_available": True,
        "aggregate_counts_match": True,
        "health_status": health,
        "forward_health_ok": health == "ok",
        "model_count": status["model_count"],
        "run_count": status["run_count"],
        "forecast_total": forecasts["total"],
        "performance_forecast_count": performance["forecast_count"],
        "matured_count": performance["matured_count"],
    }


def probe(database_url: str, expected: dict[str, int]) -> dict[str, Any]:
    if not database_url or make_url(database_url).get_backend_name() != "postgresql":
        raise RestoreReadPathError("postgresql_reader_required")
    database = RegistryDatabase(
        database_url,
        read_only=True,
        statement_timeout_ms=10_000,
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    try:
        registry = ForwardRegistry(database, actor="provider-restore-read-probe")
        return probe_registry(registry, expected)
    finally:
        database.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-counts", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, help="Optional new, private follow-up report; never overwritten"
    )
    args = parser.parse_args()
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "aggregate_read_availability",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "limitations": [
            "not_forward_health_or_source_use_approval",
            "requires_independent_permissions_and_full_restore_comparison",
            "not_provider_managed_backup_rpo_or_rto_evidence",
        ],
    }
    try:
        raw_counts = args.expected_counts.read_bytes()
        expected = parse_expected_counts(json.loads(raw_counts))
        report["expected_counts_sha256"] = hashlib.sha256(raw_counts).hexdigest()
        report.update(probe(os.environ.get("EDGAR_MOE_REGISTRY_READ_DATABASE_URL", ""), expected))
    except RestoreReadPathError as error:
        report.update(status="failed", reason=str(error), read_path_available=False)
    except Exception:
        report.update(status="failed", reason="read_probe_failed", read_path_available=False)
    encoded = json.dumps(report, sort_keys=True) + "\n"
    if args.output is not None:
        try:
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
        except OSError:
            print('{"status":"failed","reason":"report_write_failed"}')
            return 2
    print(encoded, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
