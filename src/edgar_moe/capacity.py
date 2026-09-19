"""Provider-neutral runtime, latency, and storage capacity baselines."""

from __future__ import annotations

import os
import platform
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.exc import SQLAlchemyError

from edgar_moe.api.repository import SnapshotNotFoundError, SnapshotRepository
from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.registry import ForwardRegistry

DEFAULT_BASELINE_PATHS: tuple[Path, ...] = (
    Path("data/forward"),
    Path("data/cache/forward-filings"),
    Path("data/artifacts/embedding-cache-finbert"),
    Path("data/cache/huggingface"),
)
FREE_DISK_WARNING_BYTES = 10 * 1024**3
FREE_DISK_STOP_BYTES = 2 * 1024**3


def build_capacity_baseline(
    *,
    snapshot_path: str | Path,
    database_url: str | None = None,
    api_url: str | None = None,
    paths: Sequence[str | Path] = DEFAULT_BASELINE_PATHS,
    workflow_runtime_seconds: float | None = None,
) -> dict[str, Any]:
    """Collect local observations without claiming provider or billing limits."""

    if workflow_runtime_seconds is not None and workflow_runtime_seconds < 0:
        raise ValueError("Workflow runtime cannot be negative")
    snapshot = _measure_snapshot(Path(snapshot_path))
    registry = _measure_registry(database_url)
    api = _measure_api(api_url)
    storage = [_inventory(Path(path)) for path in paths]
    disk_alerts = [
        {
            "path": item["path"],
            "status": "stop" if item.get("free_bytes", 0) < FREE_DISK_STOP_BYTES else "warning",
        }
        for item in storage
        if item.get("status") == "observed"
        and "free_bytes" in item
        and int(item.get("free_bytes", 0)) < FREE_DISK_WARNING_BYTES
    ]
    provider_measurements = {
        "managed_database_connection_count": "not_observed",
        "object_storage_bytes": "not_observed",
        "account_quotas": "not_observed",
        "provider_job_runtime": "not_observed",
    }
    return {
        "schema_version": 1,
        "baseline_type": "local_capacity",
        "observed_at": datetime.now(UTC).isoformat(),
        "status": "observed" if snapshot["status"] == "observed" else "partial",
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "snapshot": snapshot,
        "registry": registry,
        "api": api,
        "storage": storage,
        "disk_alerts": disk_alerts,
        "workflow": {
            "runtime_seconds": workflow_runtime_seconds,
            "runtime_source": "operator_input"
            if workflow_runtime_seconds is not None
            else "not_observed",
        },
        "provider_measurements": provider_measurements,
        "decision": {
            "status": "requires_provider_measurements",
            "paid_usage_approval_required": True,
            "free_disk_warning_bytes": FREE_DISK_WARNING_BYTES,
            "free_disk_stop_bytes": FREE_DISK_STOP_BYTES,
            "note": (
                "Local timings and bytes do not establish managed-service quotas, "
                "billing limits, or production SLOs."
            ),
        },
    }


def _measure_snapshot(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "status": "unavailable",
        "operations_ms": {},
    }
    try:
        result["size_bytes"] = path.stat().st_size
        repository = SnapshotRepository(path)
        cold_start = perf_counter()
        payload = repository.load()
        result["cold_load_ms"] = _milliseconds(cold_start)
        warm_start = perf_counter()
        repository.load()
        result["warm_load_ms"] = _milliseconds(warm_start)
        result["event_count"] = len(payload["events"])
        result["status"] = "observed"
        result["operations_ms"] = {
            "summary": _measure(lambda: repository.summary()),
            "events_page": _measure(lambda: repository.event_page(limit=25)),
            "equity_curve_10bps": _measure(lambda: repository.equity_curve(10)),
        }
    except (OSError, SnapshotNotFoundError, ValueError, KeyError) as error:
        result["error_type"] = type(error).__name__
    return result


def _measure_registry(database_url: str | None) -> dict[str, Any]:
    if not database_url or not database_url.strip():
        return {"status": "not_configured"}
    try:
        database = RegistryDatabase(database_url)
    except (SQLAlchemyError, ValueError) as error:
        return {"status": "unavailable", "error_type": type(error).__name__}
    registry = ForwardRegistry(database, actor="capacity-baseline")
    probes: dict[str, Callable[[], Any]] = {
        "status": registry.status,
        "performance": registry.performance,
        "runs": lambda: registry.list_runs(limit=25),
        "forecasts": lambda: registry.list_forecasts(limit=50),
        "quality_checks": lambda: registry.list_quality_checks(limit=100),
    }
    measurements: dict[str, Any] = {}
    observed = False
    try:
        for name, probe in probes.items():
            try:
                started = perf_counter()
                value = probe()
                measurements[name] = {
                    "status": "observed",
                    "latency_ms": _milliseconds(started),
                    "result_count": _result_count(value),
                }
                observed = True
            except (OSError, SQLAlchemyError, ValueError) as error:
                measurements[name] = {
                    "status": "unavailable",
                    "error_type": type(error).__name__,
                }
    finally:
        database.dispose()
    return {
        "status": "observed" if observed else "unavailable",
        "database_kind": "sqlite" if database_url.lower().startswith("sqlite") else "postgres",
        "probes": measurements,
    }


def _measure_api(api_url: str | None) -> dict[str, Any]:
    if not api_url or not api_url.strip():
        return {"status": "not_configured"}
    parsed = urlsplit(api_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"status": "unavailable", "error_type": "invalid_api_url"}
    if parsed.username or parsed.password:
        return {"status": "unavailable", "error_type": "api_url_contains_credentials"}
    origin = f"{parsed.scheme}://{parsed.netloc}"
    endpoints: dict[str, Any] = {}
    observed = 0
    unhealthy = False
    for name, path in {
        "health": "/api/v1/health",
        "summary": "/api/v1/summary",
        "freshness": "/api/v1/freshness",
    }.items():
        request = urllib.request.Request(
            f"{origin}{path}",
            headers={"Accept": "application/json", "User-Agent": "edgar-moe-capacity-baseline/1"},
        )
        try:
            started = perf_counter()
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read(1024 * 1024)
                status_code = int(response.status)
            endpoints[name] = {
                "status": "observed",
                "http_status": status_code,
                "latency_ms": _milliseconds(started),
                "body_bytes": len(body),
            }
            observed += 1
            unhealthy = unhealthy or status_code >= 400
        except urllib.error.HTTPError as error:
            endpoints[name] = {
                "status": "observed",
                "http_status": int(error.code),
                "latency_ms": _milliseconds(started),
                "body_bytes": 0,
            }
            observed += 1
            unhealthy = True
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            endpoints[name] = {"status": "unavailable", "error_type": type(error).__name__}
    return {
        "status": "unavailable" if observed == 0 else "warning" if unhealthy else "observed",
        "origin": origin,
        "endpoints": endpoints,
    }


def _inventory(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "status": "unavailable"}
    target = path.expanduser()
    try:
        if not target.exists():
            result.update({"status": "not_present", "file_count": 0, "bytes": 0})
            return result
        if target.is_file():
            result.update({"status": "observed", "file_count": 1, "bytes": target.stat().st_size})
            disk_path = target.parent
        else:
            file_count = 0
            directory_count = 0
            total_bytes = 0
            for root, directories, files in os.walk(target, followlinks=False):
                directory_count += len(directories)
                for name in files:
                    file_path = Path(root) / name
                    if file_path.is_symlink():
                        continue
                    try:
                        total_bytes += file_path.stat().st_size
                    except OSError:
                        continue
                    file_count += 1
            result.update(
                {
                    "status": "observed",
                    "file_count": file_count,
                    "directory_count": directory_count,
                    "bytes": total_bytes,
                }
            )
            disk_path = target
        stats = os.statvfs(disk_path)
        result["capacity_bytes"] = int(stats.f_frsize * stats.f_blocks)
        result["free_bytes"] = int(stats.f_frsize * stats.f_bavail)
    except OSError as error:
        result["error_type"] = type(error).__name__
    return result


def _measure(call: Callable[[], Any]) -> dict[str, Any]:
    try:
        started = perf_counter()
        value = call()
        return {
            "status": "observed",
            "latency_ms": _milliseconds(started),
            "result_count": _result_count(value),
        }
    except (OSError, ValueError, KeyError) as error:
        return {"status": "unavailable", "error_type": type(error).__name__}


def _milliseconds(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


def _result_count(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        if isinstance(value.get("items"), list):
            return len(value["items"])
        return len(value)
    return None
