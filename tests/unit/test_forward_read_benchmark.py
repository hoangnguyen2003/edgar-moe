from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_forward_read_path.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_synthetic_read_benchmark_exercises_only_local_routes() -> None:
    completed = _run("--rows", "120", "--settled-percent", "50", "--samples", "5")
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["scope"] == "synthetic_in_process_fastapi_sqlite_sequential"
    assert result["forecasts"] == 120
    assert result["settled"] == 60
    routes = result["results"]
    assert isinstance(routes, dict)
    assert set(routes) == {
        "status_uncached",
        "forecasts_page",
        "forecasts_filtered",
        "performance_warm",
        "performance_cold_interval",
    }
    assert all(route["errors"] == 0 for route in routes.values())
    assert routes["performance_cold_interval"]["requests"] == 5


@pytest.mark.parametrize("rows,percent,samples", [(119, 75, 20), (120, 101, 20), (120, 75, 4)])
def test_synthetic_read_benchmark_rejects_invalid_workloads(
    rows: int, percent: int, samples: int
) -> None:
    completed = _run(
        "--rows", str(rows), "--settled-percent", str(percent), "--samples", str(samples)
    )
    assert completed.returncode != 0
    assert "ValueError" in completed.stderr
