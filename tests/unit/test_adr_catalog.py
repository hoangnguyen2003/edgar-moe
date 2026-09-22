import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.validate_adr_catalog import validate_adr_catalog  # noqa: E402


def _write_catalog(root: Path, *, count: int) -> tuple[Path, Path, Path, Path, Path]:
    adr_dir = root / "adr"
    adr_dir.mkdir()
    for number in range(1, count + 1):
        (adr_dir / f"{number:04d}-decision.md").write_text("# Decision\n", encoding="utf-8")
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
    return adr_dir, index, readme, architecture, solution_architecture


def test_adr_catalog_matches_all_references(tmp_path: Path) -> None:
    adr_dir, index, readme, architecture, solution_architecture = _write_catalog(tmp_path, count=2)

    assert (
        validate_adr_catalog(
            adr_dir=adr_dir,
            index_path=index,
            readme_path=readme,
            architecture_path=architecture,
            solution_architecture_path=solution_architecture,
        )
        == []
    )


def test_adr_catalog_reports_stale_reference_counts(tmp_path: Path) -> None:
    adr_dir, index, readme, architecture, solution_architecture = _write_catalog(tmp_path, count=2)
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
    )

    assert "ADR index declares 1 ADRs; expected 2" in errors
    assert "README declares 1 ADRs; expected 2" in errors
    assert "architecture page declares 1 ADRs; expected 2" in errors
    assert "solution architecture declares 1 ADRs; expected 2" in errors
