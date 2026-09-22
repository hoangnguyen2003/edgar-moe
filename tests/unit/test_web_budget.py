"""The page-weight budget check must fail on real growth and only on real growth."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = REPOSITORY / "scripts" / "check_web_budget.py"
_SPEC = importlib.util.spec_from_file_location("check_web_budget", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
budget_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(budget_check)
BUDGETS = {"first_load_kb": 100, "route_chunk_kb": 50, "total_kb": 200}


def _write_bundle(
    root: Path,
    *,
    entry_bytes: int = 1_000,
    css_bytes: int = 500,
    route_bytes: int = 2_000,
) -> Path:
    """A miniature bundle shaped like the real one; text compresses, so vary it."""

    bundle = root / "public"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "index.html").write_text(
        "<!doctype html><html><head>"
        '<link rel="stylesheet" href="/assets/index.css">'
        '<script type="module" src="/assets/index.js"></script>'
        "</head><body></body></html>",
        encoding="utf-8",
    )
    filler = "const unique = %d;\n"
    (bundle / "assets" / "index.js").write_bytes(
        b"".join((filler % index).encode("utf-8") for index in range(entry_bytes))
    )
    (bundle / "assets" / "index.css").write_bytes(
        b"".join(f".rule-{index}{{color:#{index:06x}}}\n".encode() for index in range(css_bytes))
    )
    (bundle / "assets" / "RoutePage.js").write_bytes(
        b"".join((filler % (index + 10_000_000)).encode("utf-8") for index in range(route_bytes))
    )
    return bundle


def _budget_file(root: Path, **overrides: int) -> Path:
    path = root / "budget.json"
    path.write_text(json.dumps({"budgets": {**BUDGETS, **overrides}}), encoding="utf-8")
    return path


def test_a_small_bundle_fits_every_budget(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)

    measurements, errors = budget_check.measure(bundle, BUDGETS)

    assert errors == []
    assert {item.name for item in measurements} == {"first load", "total", "route chunk"}
    assert all(not item.over_budget for item in measurements)


def test_a_heavy_entry_module_breaks_the_first_load_budget(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, entry_bytes=400_000)

    measurements, errors = budget_check.measure(bundle, BUDGETS)
    first_load = next(item for item in measurements if item.name == "first load")

    assert errors == []
    assert first_load.over_budget
    assert "OVER" in first_load.render()


def test_a_heavy_route_breaks_only_the_route_budget(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, route_bytes=200_000)
    # A generous total isolates the route budget as the only one that can fail.
    budgets = {**BUDGETS, "total_kb": 10_000}

    measurements, _ = budget_check.measure(bundle, budgets)
    over = {item.name for item in measurements if item.over_budget}

    assert over == {"route chunk"}


def test_the_lazy_group_excludes_what_the_first_document_loads(tmp_path: Path) -> None:
    # A stylesheet the entry document loads is not a route chunk, however large.
    bundle = _write_bundle(tmp_path, css_bytes=200_000)

    measurements, _ = budget_check.measure(bundle, BUDGETS)
    route_chunk = next(item for item in measurements if item.name == "route chunk")

    assert "RoutePage.js" in route_chunk.detail
    assert not route_chunk.over_budget


def test_a_referenced_asset_that_is_missing_is_an_error(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    (bundle / "assets" / "index.css").unlink()

    _, errors = budget_check.measure(bundle, BUDGETS)

    assert errors == ["index.html references assets/index.css, which is not in the bundle"]


def test_a_bundle_without_an_entry_document_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "public").mkdir()

    measurements, errors = budget_check.measure(tmp_path / "public", BUDGETS)

    assert measurements == []
    assert errors and errors[0].endswith("index.html does not exist")


def test_entry_references_are_collected_once_in_document_order() -> None:
    document = (
        '<link rel="modulepreload" href="/assets/api.js">'
        '<script type="module" src="/assets/index.js"></script>'
        '<link rel="stylesheet" href="/assets/index.css">'
        '<script type="module" src="/assets/index.js"></script>'
        '<link rel="icon" href="/favicon.svg">'
    )

    assert budget_check.entry_references(document) == [
        "assets/api.js",
        "assets/index.js",
        "assets/index.css",
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{}", "missing a 'budgets' object"),
        ('{"budgets": {"first_load_kb": 1}}', "missing budget(s): route_chunk_kb, total_kb"),
    ],
)
def test_an_incomplete_budget_is_rejected(tmp_path: Path, payload: str, message: str) -> None:
    path = tmp_path / "budget.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match=re.escape(message)):
        budget_check.load_budget(path)


def test_the_committed_bundle_stays_within_the_committed_budget() -> None:
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "scripts/check_web_budget.py"],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Page weight is within budget." in completed.stdout


def test_the_cli_reports_an_overage_and_fails(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, entry_bytes=400_000)
    budget = _budget_file(tmp_path)

    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "scripts/check_web_budget.py",
            "--bundle",
            str(bundle),
            "--budget",
            str(budget),
        ],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "first load is" in completed.stderr
    assert "over its" in completed.stderr
