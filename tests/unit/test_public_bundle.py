from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR_PATH = Path(__file__).parents[2] / "scripts" / "validate_public_bundle.py"
_VALIDATOR_SPEC = importlib.util.spec_from_file_location("validate_public_bundle", _VALIDATOR_PATH)
assert _VALIDATOR_SPEC is not None and _VALIDATOR_SPEC.loader is not None
_VALIDATOR_MODULE = importlib.util.module_from_spec(_VALIDATOR_SPEC)
_VALIDATOR_SPEC.loader.exec_module(_VALIDATOR_MODULE)
validate_public_bundle = _VALIDATOR_MODULE.validate_public_bundle


def write_bundle(root: Path, *, main: str = "") -> None:
    (root / "assets").mkdir(parents=True)
    (root / ".well-known").mkdir(parents=True)
    (root / "index.html").write_text(
        '<script type="module" src="/assets/main.js"></script>',
        encoding="utf-8",
    )
    (root / "assets" / "main.js").write_text(main, encoding="utf-8")
    (root / "robots.txt").write_text(
        "User-agent: *\nDisallow: /api/\nDisallow: /api/docs\n", encoding="utf-8"
    )
    (root / ".well-known" / "security.txt").write_text(
        "Contact: https://example.test/security\n"
        "Policy: https://example.test/policy\n"
        "Preferred-Languages: en, vi\n"
        "Expires: 2027-09-19T00:00:00.000Z\n",
        encoding="utf-8",
    )


def test_public_bundle_accepts_hashed_assets_and_dynamic_chunks(tmp_path: Path) -> None:
    write_bundle(tmp_path, main='import("./chunk-abc123.js");')
    (tmp_path / "assets" / "chunk-abc123.js").write_text("export {};", encoding="utf-8")

    assert validate_public_bundle(tmp_path) == []


def test_public_bundle_rejects_missing_assets_and_source_maps(tmp_path: Path) -> None:
    write_bundle(tmp_path, main='import("./missing.js");\n//# sourceMappingURL=main.js.map')

    errors = validate_public_bundle(tmp_path)

    assert any("missing asset" in error for error in errors)
    assert any("source-map reference" in error for error in errors)


def test_public_bundle_rejects_credentials_without_printing_values(tmp_path: Path) -> None:
    write_bundle(tmp_path, main="const value = 'postgresql://user:password@example.test/db';")

    errors = validate_public_bundle(tmp_path)

    assert errors == ["database URL with password pattern found: assets/main.js"]


def test_public_bundle_rejects_reader_runtime_variable_name(tmp_path: Path) -> None:
    write_bundle(tmp_path, main="const setting = 'EDGAR_MOE_REGISTRY_READ_DATABASE_URL';")

    errors = validate_public_bundle(tmp_path)

    assert errors == ["private runtime variable name found: assets/main.js"]


def test_public_bundle_requires_disclosure_metadata(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "robots.txt").unlink()
    (tmp_path / ".well-known" / "security.txt").unlink()

    errors = validate_public_bundle(tmp_path)

    assert "public/robots.txt is missing" in errors
    assert "public/.well-known/security.txt is missing" in errors


def test_public_bundle_rejects_incomplete_security_txt(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / ".well-known" / "security.txt").write_text(
        "Contact: https://example.test/security\n", encoding="utf-8"
    )

    errors = validate_public_bundle(tmp_path)

    assert any("valid Policy field" in error for error in errors)
    assert any("valid Expires field" in error for error in errors)
