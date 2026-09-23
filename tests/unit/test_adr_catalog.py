import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.validate_adr_catalog import validate_adr_catalog  # noqa: E402


def _write_catalog(root: Path, *, count: int) -> tuple[Path, Path, Path, Path, Path]:
    adr_dir = root / "adr"
    adr_dir.mkdir()
    for number in range(1, count + 1):
        (adr_dir / f"{number:04d}-decision.md").write_text(
            f"# ADR {number:04d}: Decision\n", encoding="utf-8"
        )
    index = adr_dir / "README.md"
    index.write_text(f"All {count} records are accepted.\n", encoding="utf-8")
    readme = root / "README.md"
    readme.write_text(f"The ADR index: {count} decisions grouped by theme\n", encoding="utf-8")
    architecture = root / "ArchitecturePage.tsx"
    architecture.write_text(f"<p>Seven of the {count} recorded decisions.</p>\n", encoding="utf-8")
    solution_architecture = root / "solution-architecture.md"
    solution_architecture.write_text(
        f"- **Decision log:** [{count} architecture decision records](adr/README.md)\n",
        encoding="utf-8",
    )
    cv_entry = root / "cv-entry.md"
    cv_entry.write_text(
        f"Documented the design as a solution architecture with {count} ADRs.\n",
        encoding="utf-8",
    )
    return adr_dir, index, readme, architecture, solution_architecture, cv_entry


def test_adr_catalog_matches_all_references(tmp_path: Path) -> None:
    adr_dir, index, readme, architecture, solution_architecture, cv_entry = _write_catalog(
        tmp_path, count=2
    )

    assert (
        validate_adr_catalog(
            adr_dir=adr_dir,
            index_path=index,
            readme_path=readme,
            architecture_path=architecture,
            solution_architecture_path=solution_architecture,
            cv_entry_path=cv_entry,
        )
        == []
    )


def test_adr_catalog_reports_stale_reference_counts(tmp_path: Path) -> None:
    adr_dir, index, readme, architecture, solution_architecture, cv_entry = _write_catalog(
        tmp_path, count=2
    )
    index.write_text("All 1 records are accepted.\n", encoding="utf-8")
    readme.write_text("The ADR index: 1 decisions grouped by theme\n", encoding="utf-8")
    architecture.write_text("Seven of the 1 recorded decisions.\n", encoding="utf-8")
    solution_architecture.write_text(
        "- **Decision log:** [1 architecture decision records](adr/README.md)\n",
        encoding="utf-8",
    )

    errors = validate_adr_catalog(
        adr_dir=adr_dir,
        index_path=index,
        readme_path=readme,
        architecture_path=architecture,
        solution_architecture_path=solution_architecture,
        cv_entry_path=cv_entry,
    )

    assert "ADR index declares 1 ADRs; expected 2" in errors
    assert "README declares 1 ADRs; expected 2" in errors
    assert "architecture page declares 1 ADRs; expected 2" in errors
    assert "solution architecture declares 1 ADRs; expected 2" in errors


def test_a_record_headed_with_another_number_is_reported(tmp_path: Path) -> None:
    # Two records claiming one number is how a citation ends up pointing at the
    # wrong decision; it happened when concurrent work renumbered a file.
    adr_dir, index, readme, architecture, solution, cv_entry = _write_catalog(tmp_path, count=3)
    (adr_dir / "0003-decision.md").write_text("# ADR 0002: Decision\n", encoding="utf-8")

    errors = validate_adr_catalog(
        adr_dir=adr_dir,
        index_path=index,
        readme_path=readme,
        architecture_path=architecture,
        solution_architecture_path=solution,
        cv_entry_path=cv_entry,
    )

    assert errors == ["ADR 0003-decision.md is headed ADR 0002"]


def test_a_record_without_an_adr_heading_is_reported(tmp_path: Path) -> None:
    adr_dir, index, readme, architecture, solution, cv_entry = _write_catalog(tmp_path, count=2)
    (adr_dir / "0002-decision.md").write_text("# Decision\n", encoding="utf-8")

    errors = validate_adr_catalog(
        adr_dir=adr_dir,
        index_path=index,
        readme_path=readme,
        architecture_path=architecture,
        solution_architecture_path=solution,
        cv_entry_path=cv_entry,
    )

    assert errors == ["ADR must open with a '# ADR NNNN:' heading: 0002-decision.md"]


def test_a_stale_count_in_the_cv_entry_is_reported(tmp_path: Path) -> None:
    # The CV entry is read by people outside the repository, where a wrong
    # number is least likely to be noticed and least easy to excuse.
    adr_dir, index, readme, architecture, solution, cv_entry = _write_catalog(tmp_path, count=4)
    cv_entry.write_text(
        "Documented the design as a solution architecture with 21 ADRs.\n", encoding="utf-8"
    )

    errors = validate_adr_catalog(
        adr_dir=adr_dir,
        index_path=index,
        readme_path=readme,
        architecture_path=architecture,
        solution_architecture_path=solution,
        cv_entry_path=cv_entry,
    )

    assert errors == ["CV entry declares 21 ADRs; expected 4"]
