from __future__ import annotations

import importlib.util
import json
import struct
import zlib
from pathlib import Path

import pytest

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
    (root / "data-provenance.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "snapshot": {
                    "path": "data/demo/snapshot.json",
                    "data_mode": "authenticated_locked_test",
                    "raw_sources_public": False,
                    "derived_output_public": True,
                    "sha256": "a" * 64,
                    "selection_hash": "b" * 64,
                    "locked_test_hash": "c" * 64,
                    "research_only": True,
                },
                "review": {
                    "redistribution_status": "operator_review_required",
                    "legal_approval": False,
                },
                "sources": [
                    {
                        "id": "fixture",
                        "name": "Fixture source",
                        "role": "test data",
                        "terms_url": "https://example.test/terms",
                        "redistribution_status": "review_required",
                    }
                ],
            }
        ),
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
    (tmp_path / "data-provenance.json").unlink()

    errors = validate_public_bundle(tmp_path)

    assert "public/robots.txt is missing" in errors
    assert "public/.well-known/security.txt is missing" in errors
    assert "public/data-provenance.json is missing" in errors


def test_public_bundle_rejects_incomplete_security_txt(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / ".well-known" / "security.txt").write_text(
        "Contact: https://example.test/security\n", encoding="utf-8"
    )

    errors = validate_public_bundle(tmp_path)

    assert any("valid Policy field" in error for error in errors)
    assert any("valid Expires field" in error for error in errors)


def test_security_txt_fields_accept_crlf_but_not_a_value_on_the_next_line(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    security = tmp_path / ".well-known" / "security.txt"
    text = security.read_text(encoding="utf-8")
    security.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    assert validate_public_bundle(tmp_path) == []

    security.write_text(text.replace("Policy: https", "Policy:\nhttps"), encoding="utf-8")
    assert any("valid Policy field" in error for error in validate_public_bundle(tmp_path))


def test_security_txt_language_list_cannot_backtrack_exponentially(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    security = tmp_path / ".well-known" / "security.txt"
    text = security.read_text(encoding="utf-8")
    # The previous pattern took exponential time on this line; now it is rejected at once.
    security.write_text(
        text.replace("Preferred-Languages: en, vi", "Preferred-Languages:" + "!," * 5000),
        encoding="utf-8",
    )

    errors = validate_public_bundle(tmp_path)

    assert any("valid Preferred-Languages field" in error for error in errors)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def minimal_png(extra: bytes = b"") -> bytes:
    """A 1x1 greyscale PNG, optionally carrying an extra chunk before IEND."""
    header = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + extra
        + png_chunk(b"IEND", b"")
    )


def test_public_bundle_accepts_a_declared_pixel_only_image(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "social-card.png").write_bytes(minimal_png())

    assert validate_public_bundle(tmp_path) == []


def test_public_bundle_rejects_images_that_could_hide_content(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    card = tmp_path / "social-card.png"

    card.write_bytes(minimal_png(png_chunk(b"tEXt", b"Comment\x00private note")))
    assert any("non-pixel PNG chunk (tEXt)" in error for error in validate_public_bundle(tmp_path))

    card.write_bytes(minimal_png() + b"appended")
    assert any("trailing bytes after IEND" in error for error in validate_public_bundle(tmp_path))

    corrupt = bytearray(minimal_png())
    corrupt[-1] ^= 0xFF  # the final IEND checksum byte
    card.write_bytes(bytes(corrupt))
    assert any("corrupt PNG chunk" in error for error in validate_public_bundle(tmp_path))

    card.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200_001)
    assert any("larger than" in error for error in validate_public_bundle(tmp_path))

    # Not valid UTF-8, so it reaches the image check rather than the text review.
    card.write_bytes(b"GIF89a" + b"\xff\xfe" * 16)
    assert any("must be a PNG" in error for error in validate_public_bundle(tmp_path))


def test_public_bundle_still_rejects_undeclared_binaries(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    (tmp_path / "assets" / "extra.png").write_bytes(minimal_png())

    errors = validate_public_bundle(tmp_path)

    assert any(
        "non-text publishable file requires review: assets/extra.png" in error for error in errors
    )


_FONTS = Path(__file__).parents[2] / "apps" / "web" / "src" / "fonts"


def test_public_bundle_accepts_every_reviewed_font_under_its_hashed_build_name(
    tmp_path: Path,
) -> None:
    write_bundle(tmp_path)
    fonts = sorted(_FONTS.glob("*.woff2"))
    assert len(fonts) == 10
    for font in fonts:
        (tmp_path / "assets" / f"{font.stem}-Ab12Cd34.woff2").write_bytes(font.read_bytes())

    assert validate_public_bundle(tmp_path) == []


def test_public_bundle_rejects_fonts_that_were_not_reviewed(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    reviewed = (_FONTS / "public-sans-400-700-latin.woff2").read_bytes()
    font = tmp_path / "assets" / "public-sans-Ab12Cd34.woff2"

    altered = bytearray(reviewed)
    altered[-1] ^= 0xFF
    font.write_bytes(bytes(altered))
    assert any("not a reviewed file" in error for error in validate_public_bundle(tmp_path))

    font.write_bytes(reviewed + b"appended")
    assert any("does not match its header" in error for error in validate_public_bundle(tmp_path))

    font.write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 60)
    assert any("must be WOFF2" in error for error in validate_public_bundle(tmp_path))

    font.write_bytes(b"wOF2" + b"\x00" * 200_001)
    assert any("larger than" in error for error in validate_public_bundle(tmp_path))


def test_public_bundle_rejects_public_raw_sources(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    manifest = json.loads((tmp_path / "data-provenance.json").read_text(encoding="utf-8"))
    manifest["snapshot"]["raw_sources_public"] = True
    (tmp_path / "data-provenance.json").write_text(json.dumps(manifest), encoding="utf-8")

    errors = validate_public_bundle(tmp_path)

    assert "public provenance must declare raw_sources_public=false" in errors


def test_public_bundle_rejects_invalid_snapshot_identity(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    manifest = json.loads((tmp_path / "data-provenance.json").read_text(encoding="utf-8"))
    manifest["snapshot"]["sha256"] = "not-a-digest"
    manifest["snapshot"]["research_only"] = False
    (tmp_path / "data-provenance.json").write_text(json.dumps(manifest), encoding="utf-8")

    errors = validate_public_bundle(tmp_path)

    assert "public provenance snapshot sha256 must be a lowercase SHA-256 digest" in errors
    assert "public provenance snapshot research_only must remain true" in errors


def test_public_bundle_rejects_symlinked_assets(tmp_path: Path) -> None:
    write_bundle(tmp_path)
    target = tmp_path / "outside.js"
    target.write_text("export const private_value = 'outside';", encoding="utf-8")
    try:
        (tmp_path / "assets" / "linked.js").symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this environment")

    errors = validate_public_bundle(tmp_path)

    assert errors == ["symlink is not allowed in publishable bundle: assets/linked.js"]
