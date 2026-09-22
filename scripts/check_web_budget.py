"""Fail when the committed public bundle exceeds its declared page-weight budget.

The site is the artifact a reviewer reads, and it is served from a function that
scales to zero, so page weight decides how the first visit feels. This check
measures the compressed bytes a reader downloads before the first page renders,
the largest lazily loaded route, and the whole render path, and compares each
against ``config/web_page_weight_budget.json``.

Sizes are gzip, because that is what a browser accepts from Vercel's edge; the
numbers approximate the transfer rather than reproduce it exactly, since the
edge may use Brotli and its own compression level. Only the render path counts:
HTML, JavaScript, and CSS. Images are excluded because the largest one is the
link-preview card, which only crawlers fetch.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

BUNDLE = Path("public")
BUDGET = Path("config/web_page_weight_budget.json")

_ENTRY_REFERENCE = re.compile(r"""(?:src|href)=["']/([^"']+\.(?:js|css))["']""")
_RENDER_PATH_SUFFIXES = frozenset({".js", ".css"})
_REQUIRED_BUDGETS = ("first_load_kb", "route_chunk_kb", "total_kb")


class Measurement(NamedTuple):
    """One measured group of bytes and the budget it must respect."""

    name: str
    detail: str
    measured_bytes: int
    budget_bytes: int

    @property
    def over_budget(self) -> bool:
        return self.measured_bytes > self.budget_bytes

    def render(self) -> str:
        verdict = "OVER" if self.over_budget else "ok"
        return (
            f"{verdict:>4}  {self.name:<12} {_kb(self.measured_bytes):>8} of "
            f"{_kb(self.budget_bytes):>8}  {self.detail}"
        )


def _kb(size: int) -> str:
    return f"{size / 1024:.1f} KB"


def _gzip_size(path: Path) -> int:
    """Compressed size of one file, with a fixed timestamp so it is reproducible."""

    return len(gzip.compress(path.read_bytes(), compresslevel=6, mtime=0))


def load_budget(path: Path) -> dict[str, int]:
    """Read the declared budget, in kilobytes, keyed by the group it limits."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    budgets = payload.get("budgets")
    if not isinstance(budgets, dict):
        raise ValueError(f"{path} is missing a 'budgets' object")
    missing = [key for key in _REQUIRED_BUDGETS if key not in budgets]
    if missing:
        raise ValueError(f"{path} is missing budget(s): {', '.join(missing)}")
    return {key: int(budgets[key]) for key in _REQUIRED_BUDGETS}


def entry_references(document: str) -> list[str]:
    """Bundle-relative paths the first document loads before anything renders."""

    seen: dict[str, None] = {}
    for reference in _ENTRY_REFERENCE.findall(document):
        seen.setdefault(reference, None)
    return list(seen)


def measure(bundle: Path, budgets: dict[str, int]) -> tuple[list[Measurement], list[str]]:
    """Measure the render path; returns the measurements and any structural errors."""

    errors: list[str] = []
    index = bundle / "index.html"
    if not index.is_file():
        return [], [f"{index} does not exist"]

    document = index.read_text(encoding="utf-8")
    first_load = {"index.html": _gzip_size(index)}
    for reference in entry_references(document):
        asset = bundle / reference
        if not asset.is_file():
            errors.append(f"index.html references {reference}, which is not in the bundle")
            continue
        first_load[reference] = _gzip_size(asset)

    render_path = {
        path.relative_to(bundle).as_posix(): _gzip_size(path)
        for path in sorted(bundle.rglob("*"))
        if path.is_file() and path.suffix in _RENDER_PATH_SUFFIXES
    }
    if not render_path:
        errors.append(f"{bundle} contains no JavaScript or CSS")
        return [], errors

    lazy = {name: size for name, size in render_path.items() if name not in first_load}
    total = sum(render_path.values()) + first_load["index.html"]
    measurements = [
        Measurement(
            name="first load",
            detail=f"{len(first_load)} files: " + ", ".join(sorted(first_load)),
            measured_bytes=sum(first_load.values()),
            budget_bytes=budgets["first_load_kb"] * 1024,
        ),
        Measurement(
            name="total",
            detail=f"{len(render_path) + 1} files on the render path",
            measured_bytes=total,
            budget_bytes=budgets["total_kb"] * 1024,
        ),
    ]
    if lazy:
        heaviest = max(lazy, key=lambda name: lazy[name])
        measurements.append(
            Measurement(
                name="route chunk",
                detail=f"heaviest of {len(lazy)} lazily loaded files: {heaviest}",
                measured_bytes=lazy[heaviest],
                budget_bytes=budgets["route_chunk_kb"] * 1024,
            )
        )
    return measurements, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=BUNDLE, help="committed bundle directory")
    parser.add_argument("--budget", type=Path, default=BUDGET, help="declared budget file")
    parser.add_argument("--json", action="store_true", help="print the measurements as JSON")
    args = parser.parse_args()

    try:
        budgets = load_budget(args.budget)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"budget could not be read: {error}", file=sys.stderr)
        return 1

    measurements, errors = measure(args.bundle, budgets)
    if args.json:
        print(
            json.dumps(
                {
                    "measurements": [item._asdict() for item in measurements],
                    "errors": errors,
                },
                indent=2,
            )
        )
    else:
        for item in measurements:
            print(item.render())
        for message in errors:
            print(message, file=sys.stderr)

    over = [item for item in measurements if item.over_budget]
    if errors or over:
        for item in over:
            excess = item.measured_bytes - item.budget_bytes
            print(
                f"{item.name} is {_kb(excess)} over its {_kb(item.budget_bytes)} budget",
                file=sys.stderr,
            )
        return 1
    if not args.json:
        print("Page weight is within budget.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
