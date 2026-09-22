"""Validate that the committed Vite bundle is safe to publish."""

from __future__ import annotations

import json
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
    r"EDGAR_MOE_(?:REGISTRY_(?:DATABASE_URL|READ_DATABASE_URL)|R2_[A-Z_]+))\b"
)
_SOURCE_MAP_REFERENCE = re.compile(r"sourceMappingURL|[A-Za-z0-9._/-]+\.(?:js|css)\.map")
_ASSET_REFERENCE = re.compile(r"[\"`]((?:/|\./)?(?:assets/)?[A-Za-z0-9._/-]+\.(?:js|css))[\"`]")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_SNAPSHOT_PATH = "data/demo/snapshot.json"
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
