"""Reject credential-bearing or unsafe files before operational evidence upload."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_DEFAULT_MAX_BYTES = 4 * 1024 * 1024
_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
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
        re.compile(r"\b(?:postgres(?:ql)?|mysql|redis)://[^\"'\s]+:[^\"'\s]+@"),
    ),
    (
        "URL with credentials",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
    ),
    (
        "credential assignment",
        re.compile(
            r"(?i)[\"']?(?:api[_-]?key|access[_-]?key(?:[_-]?id)?|"
            r"secret(?:[_-]?access[_-]?key)?|password|passwd|token|private[_-]?key|"
            r"database[_-]?url|connection[_-]?string)[\"']?\s*[:=]\s*"
            r"[\"']?(?!null\b|true\b|false\b)[^\"'\s,;}]+"
        ),
    ),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b")),
)


def validate_redacted_artifacts(
    root: Path = Path("reports"), *, max_bytes: int = _DEFAULT_MAX_BYTES
) -> list[str]:
    """Return safe, value-free findings for a retained evidence directory."""
    root = root.expanduser().resolve()
    if max_bytes <= 0:
        return ["maximum file size must be positive"]
    if not root.is_dir():
        return [f"evidence directory is missing: {root}"]

    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            findings.append(f"symlink is not allowed in retained evidence: {relative}")
            continue
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            if size > max_bytes:
                findings.append(f"evidence file exceeds {max_bytes} bytes: {relative}")
                continue
            raw = path.read_bytes()
        except OSError:
            findings.append(f"evidence file is not readable: {relative}")
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(f"non-text evidence file requires review: {relative}")
            continue
        for label, pattern in _SENSITIVE_PATTERNS:
            if pattern.search(text):
                findings.append(f"{label} pattern found: {relative}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=_DEFAULT_MAX_BYTES)
    args = parser.parse_args()
    findings = validate_redacted_artifacts(args.root, max_bytes=args.max_bytes)
    if findings:
        for finding in findings:
            print(f"ERROR: {finding}", file=sys.stderr)
        return 1
    print(f"Redacted artifact validation passed: {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
