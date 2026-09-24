"""Validate that the committed Vite bundle is safe to publish."""

from __future__ import annotations

import hashlib
import json
import re
import struct
import sys
import zlib
from pathlib import Path

_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "private key header",
        re.compile(r"-----BEGIN (?:RSA|EC|OPENSSH|PRIVATE) KEY-----"),
    ),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("OpenAI-style token", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    (
        "database URL with password",
        re.compile(r"\bpostgres(?:ql)?://[^\"'\s]+:[^\"'\s]+@"),
    ),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b")),
)

_PRIVATE_RUNTIME_NAMES = re.compile(
    r"\b(?:ALPACA_API_(?:KEY|SECRET)|FRED_API_KEY|SEC_USER_AGENT|"
    r"EDGAR_MOE_(?:REGISTRY_(?:DATABASE_URL|READ_DATABASE_URL)|R2_[A-Z_]+))\b"
)
_SOURCE_MAP_REFERENCE = re.compile(r"sourceMappingURL|[A-Za-z0-9._/-]+\.(?:js|css)\.map")
_ASSET_REFERENCE = re.compile(r"[\"`]((?:/|\./)?(?:assets/)?[A-Za-z0-9._/-]+\.(?:js|css))[\"`]")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_SNAPSHOT_PATH = "data/demo/snapshot.json"
# Images cannot be reviewed as text, so only these exact files may be binary. Each
# must be a PNG carrying pixel data alone: no text, EXIF, or colour-profile chunk
# can smuggle content into the public bundle, and every chunk checksum must match.
_REVIEWED_IMAGES = frozenset({"apple-touch-icon.png", "social-card.png"})
_MAX_IMAGE_BYTES = 200_000
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_ALLOWED_PNG_CHUNKS = frozenset(
    {"IHDR", "PLTE", "tRNS", "IDAT", "IEND", "sRGB", "gAMA", "cHRM", "pHYs"}
)
# Fonts cannot be reviewed as text either. Only these exact files may ship: the
# site's latin and latin-ext subsets (apps/web/src/fonts.css), identified by
# content because the build gives them hashed names. Replacing a font means
# reviewing it and updating its digest here.
_REVIEWED_FONT_DIGESTS = frozenset(
    {
        "f1050dc5317b43434c0aeda599d4624c774ffc162e87a8cf204b949b6a85816d",  # ibm-plex-mono-400-latin-ext.woff2
        "c36f509c0a8f9f85f29cb44bc8701d8a9e0b14c499e77a884f789ead7093a7ac",  # ibm-plex-mono-400-latin.woff2
        "77f03e26f981c582bdba3a7abed4baa2d3149211c01366bb3ab3ba7622ec4ae5",  # ibm-plex-mono-500-latin-ext.woff2
        "a76f53ca6612e7b3828eec2311098675b7f9849ae4169a8bcef6302aec02a6c0",  # ibm-plex-mono-500-latin.woff2
        "1b6b18fd0fd240bc6d5850f4df621484722d4b5d3650ebdd1e3a8bbd81c75854",  # ibm-plex-mono-600-latin-ext.woff2
        "ad4580d8cb4b5f627c2d18457656732f7f7b070f7837fbc380e08054157e6f6c",  # ibm-plex-mono-600-latin.woff2
        "cbca001188852d514d8654be7ddc97868f039bdf986b926ec5c985c117853cfd",  # newsreader-400-700-latin-ext.woff2
        "01817351be3edfc1714fe6d60ddea6a22a169a5ebd033b50c7f9495e5d9c386a",  # newsreader-400-700-latin.woff2
        "1c8a26c0ed4312dacc36670714794c9f4b4a0972ccb45f2936646bd8d444d5f8",  # public-sans-400-700-latin-ext.woff2
        "c1b6da516e0062e9c2f341b3a51dd2d621d946da72f06c6cfe05fd9d2dd8622d",  # public-sans-400-700-latin.woff2
    }
)
_MAX_FONT_BYTES = 200_000
_WOFF2_SIGNATURE = b"wOF2"
_WOFF2_HEADER_BYTES = 48
# security.txt fields: a value must sit on its field's own line ([^\S\n] is any
# whitespace except a newline, which still allows CRLF endings), and a language
# tag cannot contain a comma, so the list pattern matches in only one way and
# cannot backtrack exponentially.
_SECURITY_CONTACT = re.compile(r"(?m)^Contact:[^\S\n]*\S+[^\S\n]*$")
_SECURITY_POLICY = re.compile(r"(?m)^Policy:[^\S\n]*\S+[^\S\n]*$")
_SECURITY_LANGUAGES = re.compile(
    r"(?m)^Preferred-Languages:[^\S\n]*[^\s,]+(?:[^\S\n]*,[^\S\n]*[^\s,]+)*[^\S\n]*$"
)
_SECURITY_EXPIRES = re.compile(
    r"(?m)^Expires:[^\S\n]*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z[^\S\n]*$"
)
_ISO_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_HTTPS_URL = re.compile(r"^https://\S+$")


def validate_public_bundle(root: Path = Path("public")) -> list[str]:
    """Return safe, value-redacted validation errors for a published bundle."""
    errors: list[str] = []
    if not root.is_dir():
        return [f"public bundle directory is missing: {root}"]

    index = root / "index.html"
    if not index.is_file():
        errors.append("public/index.html is missing")

    _validate_disclosure_metadata(root, errors)
    _validate_provenance_manifest(root, errors)

    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            errors.append(f"symlink is not allowed in publishable bundle: {relative}")
        elif path.is_file():
            files.append(path)
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.suffix == ".map" or path.name.startswith(".env"):
            errors.append(f"forbidden publishable file: {relative}")
            continue
        # A font is checked as a font even if its bytes happen to decode as text.
        if path.suffix == ".woff2":
            _validate_reviewed_font(path, relative, errors)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            if relative in _REVIEWED_IMAGES:
                _validate_reviewed_image(path, relative, errors)
            else:
                errors.append(f"non-text publishable file requires review: {relative}")
            continue
        if _SOURCE_MAP_REFERENCE.search(text):
            errors.append(f"source-map reference found: {relative}")
        if _PRIVATE_RUNTIME_NAMES.search(text):
            errors.append(f"private runtime variable name found: {relative}")
        for label, pattern in _CREDENTIAL_PATTERNS:
            if pattern.search(text):
                errors.append(f"{label} pattern found: {relative}")

        for reference in _ASSET_REFERENCE.findall(text):
            referenced = _resolve_asset_reference(path, root, reference)
            if referenced is not None and not referenced.is_file():
                errors.append(
                    f"missing asset referenced by {relative}: {referenced.relative_to(root)}"
                )

    return errors


def _validate_reviewed_image(path: Path, relative: str, errors: list[str]) -> None:
    """Accept a declared image only as bounded PNG pixel data with intact checksums."""
    data = path.read_bytes()
    if len(data) > _MAX_IMAGE_BYTES:
        errors.append(f"publishable image is larger than {_MAX_IMAGE_BYTES} bytes: {relative}")
        return
    if not data.startswith(_PNG_SIGNATURE):
        errors.append(f"publishable image must be a PNG: {relative}")
        return
    offset = len(_PNG_SIGNATURE)
    seen_end = False
    while offset < len(data):
        if seen_end:
            errors.append(f"publishable image has trailing bytes after IEND: {relative}")
            return
        if offset + 8 > len(data):
            errors.append(f"publishable image has a truncated PNG chunk: {relative}")
            return
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if length > len(data) or end > len(data):
            errors.append(f"publishable image has a truncated PNG chunk: {relative}")
            return
        name = kind.decode("ascii", errors="replace")
        if name not in _ALLOWED_PNG_CHUNKS:
            errors.append(f"publishable image carries a non-pixel PNG chunk ({name}): {relative}")
            return
        (stored_crc,) = struct.unpack(">I", data[end - 4 : end])
        if zlib.crc32(data[offset + 4 : end - 4]) & 0xFFFFFFFF != stored_crc:
            errors.append(f"publishable image has a corrupt PNG chunk ({name}): {relative}")
            return
        seen_end = name == "IEND"
        offset = end
    if not seen_end:
        errors.append(f"publishable image is missing its PNG end marker: {relative}")


def _validate_reviewed_font(path: Path, relative: str, errors: list[str]) -> None:
    """Accept a font only as a well-formed WOFF2 file that is one of the reviewed files."""
    data = path.read_bytes()
    if len(data) > _MAX_FONT_BYTES:
        errors.append(f"publishable font is larger than {_MAX_FONT_BYTES} bytes: {relative}")
        return
    if len(data) < _WOFF2_HEADER_BYTES or not data.startswith(_WOFF2_SIGNATURE):
        errors.append(f"publishable font must be WOFF2: {relative}")
        return
    # The header's total-length field must describe exactly this file, so nothing
    # can ride along after the font data.
    (declared_length,) = struct.unpack(">I", data[8:12])
    if declared_length != len(data):
        errors.append(f"publishable font length does not match its header: {relative}")
        return
    if hashlib.sha256(data).hexdigest() not in _REVIEWED_FONT_DIGESTS:
        errors.append(f"publishable font is not a reviewed file: {relative}")


def _validate_disclosure_metadata(root: Path, errors: list[str]) -> None:
    """Require the low-cost, provider-neutral public disclosure files."""
    robots = root / "robots.txt"
    if not robots.is_file():
        errors.append("public/robots.txt is missing")
    else:
        try:
            robots_text = robots.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append("public/robots.txt must be UTF-8 text")
        else:
            if "User-agent: *" not in robots_text:
                errors.append("public/robots.txt must define a wildcard user-agent")
            if "Disallow: /api/" not in robots_text:
                errors.append("public/robots.txt must disallow /api/ indexing")

    security = root / ".well-known" / "security.txt"
    if not security.is_file():
        errors.append("public/.well-known/security.txt is missing")
        return
    try:
        security_text = security.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append("public/.well-known/security.txt must be UTF-8 text")
        return
    for pattern, field in (
        (_SECURITY_CONTACT, "Contact"),
        (_SECURITY_POLICY, "Policy"),
        (_SECURITY_LANGUAGES, "Preferred-Languages"),
        (_SECURITY_EXPIRES, "Expires"),
    ):
        if not pattern.search(security_text):
            errors.append(f"public/.well-known/security.txt is missing a valid {field} field")


def _validate_provenance_manifest(root: Path, errors: list[str]) -> None:
    """Require an explicit, conservative source and redistribution contract."""
    manifest_path = root / "data-provenance.json"
    if not manifest_path.is_file():
        errors.append("public/data-provenance.json is missing")
        return
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append("public/data-provenance.json is not readable JSON")
        return
    if not isinstance(payload, dict):
        errors.append("public/data-provenance.json must be a JSON object")
        return
    if payload.get("schema_version") != 1:
        errors.append("public/data-provenance.json schema_version must be 1")

    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        errors.append("public/data-provenance.json snapshot must be an object")
    else:
        if snapshot.get("path") != _EXPECTED_SNAPSHOT_PATH:
            errors.append("public provenance snapshot path must be " + _EXPECTED_SNAPSHOT_PATH)
        if snapshot.get("data_mode") != "authenticated_locked_test":
            errors.append("public provenance data_mode must remain authenticated_locked_test")
        if not _SHA256.fullmatch(str(snapshot.get("sha256", ""))):
            errors.append("public provenance snapshot sha256 must be a lowercase SHA-256 digest")
        for field in ("selection_hash", "locked_test_hash"):
            if not _SHA256.fullmatch(str(snapshot.get(field, ""))):
                errors.append(
                    f"public provenance snapshot {field} must be a lowercase SHA-256 digest"
                )
        if snapshot.get("research_only") is not True:
            errors.append("public provenance snapshot research_only must remain true")
        if snapshot.get("raw_sources_public") is not False:
            errors.append("public provenance must declare raw_sources_public=false")
        if snapshot.get("derived_output_public") is not True:
            errors.append("public provenance must declare derived_output_public=true")

    review = payload.get("review")
    if not isinstance(review, dict):
        errors.append("public/data-provenance.json review must be an object")
    else:
        redistribution_status = review.get("redistribution_status")
        if redistribution_status not in {"operator_review_required", "approved"}:
            errors.append(
                "public provenance redistribution status must be operator_review_required or approved"
            )
        legal_approval = review.get("legal_approval")
        if legal_approval not in {False, True}:
            errors.append("public provenance legal_approval must be a boolean")
        last_reviewed_at = review.get("last_reviewed_at")
        if redistribution_status == "approved":
            if legal_approval is not True:
                errors.append("approved public provenance requires legal_approval=true")
            if not isinstance(last_reviewed_at, str) or not _ISO_TIMESTAMP.fullmatch(
                last_reviewed_at
            ):
                errors.append(
                    "approved public provenance requires a UTC last_reviewed_at timestamp"
                )
        else:
            if legal_approval is not False:
                errors.append(
                    "operator-review-required public provenance must keep legal_approval=false"
                )
            if last_reviewed_at is not None and (
                not isinstance(last_reviewed_at, str)
                or not _ISO_TIMESTAMP.fullmatch(last_reviewed_at)
            ):
                errors.append("public provenance last_reviewed_at must be a UTC timestamp or null")

    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("public provenance sources must be a non-empty list")
    else:
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"public provenance source {index} must be an object")
                continue
            if not all(
                isinstance(source.get(field), str) and source[field]
                for field in ("id", "name", "role")
            ):
                errors.append(f"public provenance source {index} is missing identity fields")
            if not _HTTPS_URL.fullmatch(str(source.get("terms_url", ""))):
                errors.append(f"public provenance source {index} must have an HTTPS terms_url")
            if source.get("redistribution_status") not in {"review_required", "approved"}:
                errors.append(
                    f"public provenance source {index} redistribution status must be review_required or approved"
                )


def _resolve_asset_reference(source: Path, root: Path, reference: str) -> Path | None:
    if reference.startswith("/"):
        return root / reference.lstrip("/")
    if reference.startswith("./"):
        return source.parent / reference.removeprefix("./")
    if reference.startswith("assets/"):
        return root / reference
    return None


def main() -> int:
    errors = validate_public_bundle()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Public bundle validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
