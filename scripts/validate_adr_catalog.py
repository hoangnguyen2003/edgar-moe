"""Validate that architecture decision counts stay synchronized.

The ADR index is referenced by the README and the public architecture page.
This check keeps those portfolio-facing summaries aligned with the numbered
records on disk instead of relying on a manual count.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ADR_DIR = Path("docs/adr")
ADR_INDEX = ADR_DIR / "README.md"
README = Path("README.md")
ARCHITECTURE_PAGE = Path("apps/web/src/pages/ArchitecturePage.tsx")
SOLUTION_ARCHITECTURE = Path("docs/solution-architecture.md")

_ADR_NAME = re.compile(r"^(?P<number>[0-9]{4})-[a-z0-9][a-z0-9-]*\.md$")
_ADR_HEADING = re.compile(r"^#\s+ADR\s+(?P<number>[0-9]{4})\b")
_INDEX_COUNT = re.compile(r"\bAll\s+(?P<count>[0-9]+)\s+records\b")
_README_COUNT = re.compile(r"\bADR index\b[^\n]*?:\s*(?P<count>[0-9]+)\s+decisions\b")
_PAGE_COUNT = re.compile(r"\bof\s+the\s+(?P<count>[0-9]+)\s+recorded decisions\b")
_SOLUTION_COUNT = re.compile(
    r"\bDecision log:\s*\*{0,2}\s*\[(?P<count>[0-9]+)\s+architecture decision records\]"
)


def validate_adr_catalog(
    *,
    adr_dir: Path = ADR_DIR,
    index_path: Path = ADR_INDEX,
    readme_path: Path = README,
    architecture_path: Path = ARCHITECTURE_PAGE,
    solution_architecture_path: Path = SOLUTION_ARCHITECTURE,
) -> list[str]:
    """Return violations of the ADR count and numbering contract."""

    errors: list[str] = []
    records = sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md"))
    if not records:
        errors.append(f"no numbered ADR records found in {adr_dir}")
        return errors

    numbers: list[int] = []
    for record in records:
        match = _ADR_NAME.fullmatch(record.name)
        if match is None:
            errors.append(f"ADR filename is invalid: {record.name}")
            continue
        numbers.append(int(match.group("number")))
        # A record that names a different number than its filename is filed
        # under one identity and cited under another.
        heading = _ADR_HEADING.match(record.read_text(encoding="utf-8").lstrip())
        if heading is None:
            errors.append(f"ADR must open with a '# ADR NNNN:' heading: {record.name}")
        elif heading.group("number") != match.group("number"):
            errors.append(f"ADR {record.name} is headed ADR {heading.group('number')}")
    expected_numbers = list(range(1, len(records) + 1))
    if numbers != expected_numbers:
        errors.append(
            "ADR numbers must be contiguous from 0001 through "
            f"{len(records):04d}; found {', '.join(f'{number:04d}' for number in numbers)}"
        )

    expected_count = len(records)
    for path, pattern, label in (
        (index_path, _INDEX_COUNT, "ADR index"),
        (readme_path, _README_COUNT, "README"),
        (architecture_path, _PAGE_COUNT, "architecture page"),
        (solution_architecture_path, _SOLUTION_COUNT, "solution architecture"),
    ):
        text = _read(path, label, errors)
        if text is None:
            continue
        match = pattern.search(text)
        if match is None:
            errors.append(f"{label} does not declare the ADR count")
            continue
        declared = int(match.group("count"))
        if declared != expected_count:
            errors.append(f"{label} declares {declared} ADRs; expected {expected_count}")
    return errors


def _read(path: Path, label: str, errors: list[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        errors.append(f"{label} is not readable ({type(error).__name__})")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adr-dir", type=Path, default=ADR_DIR)
    parser.add_argument("--index", type=Path, default=ADR_INDEX)
    parser.add_argument("--readme", type=Path, default=README)
    parser.add_argument("--architecture-page", type=Path, default=ARCHITECTURE_PAGE)
    parser.add_argument("--solution-architecture", type=Path, default=SOLUTION_ARCHITECTURE)
    args = parser.parse_args()
    errors = validate_adr_catalog(
        adr_dir=args.adr_dir,
        index_path=args.index,
        readme_path=args.readme,
        architecture_path=args.architecture_page,
        solution_architecture_path=args.solution_architecture,
    )
    if errors:
        print("ADR catalog contract failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("ADR catalog contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
