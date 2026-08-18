"""Validate that the committed Vite bundle is safe to publish."""

from __future__ import annotations

import re
import sys
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
    r"EDGAR_MOE_(?:REGISTRY_DATABASE_URL|R2_[A-Z_]+))\b"
)
_SOURCE_MAP_REFERENCE = re.compile(r"sourceMappingURL|[A-Za-z0-9._/-]+\.(?:js|css)\.map")
_ASSET_REFERENCE = re.compile(r"[\"`]((?:/|\./)?(?:assets/)?[A-Za-z0-9._/-]+\.(?:js|css))[\"`]")


def validate_public_bundle(root: Path = Path("public")) -> list[str]:
    """Return safe, value-redacted validation errors for a published bundle."""
    errors: list[str] = []
    if not root.is_dir():
        return [f"public bundle directory is missing: {root}"]

    index = root / "index.html"
    if not index.is_file():
        errors.append("public/index.html is missing")

    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.suffix == ".map" or path.name.startswith(".env"):
            errors.append(f"forbidden publishable file: {relative}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
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
