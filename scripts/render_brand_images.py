"""Render the site's link preview and touch icon as metadata-free PNGs.

The public bundle accepts exactly two images, and only as PNG pixel data
(ADR 0024). Both are drawn from sources that can be reviewed as text:

- ``apps/web/brand/social-card.html`` becomes ``social-card.png`` (1200x630);
- ``apps/web/public/favicon.svg`` becomes ``apple-touch-icon.png`` (180x180),
  full-bleed, because iOS rounds an icon's corners itself.

The pages are served over local HTTP, since Chrome refuses web fonts from
``file://`` pages, and screenshotted in headless Chrome after their fonts load.
Every chunk except pixel data is then dropped, so no text, EXIF, or colour
profile reaches the bundle.

Usage: ``uv run python scripts/render_brand_images.py`` (requires Google Chrome).
"""

from __future__ import annotations

import argparse
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
PUBLIC = WEB / "public"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# The chunks scripts/validate_public_bundle.py accepts in a published image.
PIXEL_CHUNKS = frozenset({"IHDR", "PLTE", "tRNS", "IDAT", "IEND", "sRGB", "gAMA", "cHRM", "pHYs"})
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)
# The icon's own field fills the square; its mark sits inset, so the corners iOS
# rounds off never come near the letter.
TOUCH_ICON_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
html, body {{ margin: 0; background: {field}; }}
svg {{ display: block; width: 150px; height: 150px; margin: 15px; }}
</style></head><body>{svg}</body></html>
"""


def pixel_chunks_only(data: bytes) -> bytes:
    """Return the PNG with every chunk dropped except pixel and rendering data."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG")
    kept = bytearray(PNG_SIGNATURE)
    offset = len(PNG_SIGNATURE)
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        name = data[offset + 4 : offset + 8].decode("latin-1")
        end = offset + 12 + length
        if end > len(data):
            raise ValueError(f"truncated PNG chunk {name}")
        if name in PIXEL_CHUNKS:
            kept += data[offset:end]
        offset = end
        if name == "IEND":
            return bytes(kept)
    raise ValueError("PNG has no IEND chunk")


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height from the IHDR chunk, which always comes first."""
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def full_bleed(svg: str) -> str:
    """The favicon without its corner radius, since iOS masks the touch icon."""
    return re.sub(r'(<rect width="64" height="64") rx="[0-9.]+"', r"\1", svg, count=1)


def field_colour(svg: str) -> str:
    """The fill of the favicon's background square."""
    match = re.search(r'<rect width="64" height="64"[^>]*fill="(#[0-9a-fA-F]{6})"', svg)
    if not match:
        raise ValueError("favicon.svg has no 64x64 background square")
    return match.group(1)


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return found
    raise SystemExit("Google Chrome is required to render the brand images.")


def screenshot(chrome: str, url: str, width: int, height: int) -> bytes:
    """Capture a page once it has settled; headless Chrome often never exits by itself."""
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(scratch) / "shot.png"
        process = subprocess.Popen(  # noqa: S603 - fixed binary, local URL
            [
                chrome,
                "--headless=new",
                f"--user-data-dir={scratch}/profile",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                f"--window-size={width},{height}",
                "--virtual-time-budget=8000",
                f"--screenshot={target}",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            previous = -1
            for _ in range(120):
                time.sleep(0.25)
                size = target.stat().st_size if target.exists() else -1
                if size > 0 and size == previous:
                    return target.read_bytes()
                previous = size
            raise SystemExit(f"Chrome produced no screenshot of {url}")
        finally:
            process.kill()
            process.wait()


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - base signature
        return


def render(out_dir: Path) -> list[Path]:
    chrome = find_chrome()
    # Served from apps/web, so the card's relative font URLs resolve.
    icon_page = WEB / "brand" / ".touch-icon.html"
    svg = (PUBLIC / "favicon.svg").read_text(encoding="utf-8")
    page = TOUCH_ICON_PAGE.format(field=field_colour(svg), svg=full_bleed(svg))
    icon_page.write_text(page, encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(WEB)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    jobs = (
        (f"{origin}/brand/social-card.html", (1200, 630), "social-card.png"),
        (f"{origin}/brand/.touch-icon.html", (180, 180), "apple-touch-icon.png"),
    )
    written: list[Path] = []
    try:
        for url, (width, height), name in jobs:
            image = pixel_chunks_only(screenshot(chrome, url, width, height))
            if png_size(image) != (width, height):
                raise SystemExit(f"{name} rendered at {png_size(image)}, not {(width, height)}")
            target = out_dir / name
            target.write_bytes(image)
            written.append(target)
    finally:
        server.shutdown()
        icon_page.unlink(missing_ok=True)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=PUBLIC, help="where to write the PNGs")
    args = parser.parse_args()
    for path in render(args.out_dir):
        shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        print(f"{shown}: {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
