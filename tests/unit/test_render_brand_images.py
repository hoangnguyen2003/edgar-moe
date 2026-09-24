from __future__ import annotations

import importlib.util
import struct
import zlib
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).parents[2] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render = _load("render_brand_images")
validator = _load("validate_public_bundle")


def png_chunk(name: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + name
        + data
        + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
    )


def screenshot_like_png(width: int = 3, height: int = 2) -> bytes:
    """A small RGB PNG carrying the metadata a browser screenshot can add."""
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\xe2\xf2\x5c" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"iCCP", b"sRGB\x00\x00" + zlib.compress(b"profile"))
        + png_chunk(b"tEXt", b"Software\x00Chrome")
        + png_chunk(b"IDAT", zlib.compress(rows))
        + png_chunk(b"eXIf", b"MM\x00*")
        + png_chunk(b"IEND", b"")
    )


def test_rendered_images_keep_only_what_the_bundle_validator_accepts(tmp_path: Path) -> None:
    raw = screenshot_like_png()
    card = tmp_path / "social-card.png"

    card.write_bytes(raw)
    rejected: list[str] = []
    validator._validate_reviewed_image(card, "social-card.png", rejected)
    assert rejected, "the unstripped screenshot should fail the gate"

    card.write_bytes(render.pixel_chunks_only(raw))
    accepted: list[str] = []
    validator._validate_reviewed_image(card, "social-card.png", accepted)
    assert accepted == []
    assert render.png_size(card.read_bytes()) == (3, 2)


def test_stripping_rejects_what_is_not_a_whole_png() -> None:
    with pytest.raises(ValueError, match="not a PNG"):
        render.pixel_chunks_only(b"GIF89a")
    with pytest.raises(ValueError, match="no IEND"):
        render.pixel_chunks_only(screenshot_like_png()[:-12])


def test_touch_icon_is_the_favicon_full_bleed_on_its_own_field() -> None:
    favicon = (Path(__file__).parents[2] / "apps" / "web" / "public" / "favicon.svg").read_text(
        encoding="utf-8"
    )

    bleed = render.full_bleed(favicon)

    # iOS rounds the corners itself, so the square must not be rounded twice.
    assert 'rx="' in favicon
    assert '<rect width="64" height="64" fill=' in bleed
    assert render.field_colour(favicon) == "#e2f25c"
