"""Fail when a tracked Markdown file links to a repository path or heading that does not exist.

The architecture and governance documents cite code, tests, workflows, and
each other as evidence. This check keeps those citations honest: every relative
link must resolve inside the repository, and every ``#anchor`` on a Markdown
target must match one of its headings as GitHub renders them. External URLs are
not fetched.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote

_INLINE_LINK = re.compile(
    r"!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*<?([^()\s>]+(?:\([^()\s]*\))?)>?(?:\s+\"[^\"]*\")?\s*\)"
)
_REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+\"[^\"]*\")?\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+)(.+?)\1")
_ATX_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_HTML_ANCHOR = re.compile(r"<a\s+(?:name|id)=\"([^\"]+)\"", re.IGNORECASE)
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)
_MARKDOWN_LINK_TEXT = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>]+>")
# GitHub keeps letters, digits, underscores, hyphens, and spaces when slugging a heading.
_SLUG_DROP = re.compile(r"[^\w\- ]", re.UNICODE)


class BrokenLink(NamedTuple):
    source: Path
    line: int
    target: str
    reason: str

    def render(self, root: Path) -> str:
        return (
            f"{self.source.relative_to(root).as_posix()}:{self.line}: {self.target} ({self.reason})"
        )


def github_slug(heading: str) -> str:
    """Return the anchor GitHub generates for a heading's text."""
    text = _MARKDOWN_LINK_TEXT.sub(r"\1", heading)
    text = _HTML_TAG.sub("", text)
    return _SLUG_DROP.sub("", text.strip().lower()).replace(" ", "-")


def heading_anchors(markdown: str) -> set[str]:
    """Every anchor a Markdown document exposes, including duplicate-heading suffixes."""
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for line in _prose_lines(markdown):
        anchors.update(match.group(1) for match in _HTML_ANCHOR.finditer(line[1]))
        heading = _ATX_HEADING.match(line[1])
        if heading is None:
            continue
        slug = github_slug(heading.group(1))
        seen = counts.get(slug, 0)
        counts[slug] = seen + 1
        anchors.add(slug if seen == 0 else f"{slug}-{seen}")
    return anchors


def _prose_lines(markdown: str) -> list[tuple[int, str]]:
    """Numbered lines outside fenced code blocks, with inline code removed."""
    lines: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(markdown.splitlines(), start=1):
        marker = _FENCE.match(line)
        if marker is not None:
            token = marker.group(1)
            if fence is None:
                fence = token[0] * 3
            elif token.startswith(fence):
                fence = None
            continue
        if fence is None:
            lines.append((number, _INLINE_CODE.sub("", line)))
    return lines


def link_targets(markdown: str) -> list[tuple[int, str]]:
    """Inline, image, and reference-definition link targets with their line numbers."""
    targets: list[tuple[int, str]] = []
    for number, line in _prose_lines(markdown):
        targets.extend((number, match.group(1)) for match in _INLINE_LINK.finditer(line))
        reference = _REFERENCE_LINK.match(line)
        if reference is not None:
            targets.append((number, reference.group(1)))
    return targets


def check_file(path: Path, root: Path, anchor_cache: dict[Path, set[str]]) -> list[BrokenLink]:
    """Return the broken repository links in one Markdown file."""
    broken: list[BrokenLink] = []
    markdown = path.read_text(encoding="utf-8")
    for line, target in link_targets(markdown):
        if _EXTERNAL.match(target):
            continue
        location, _, anchor = target.partition("#")
        destination = (path.parent / unquote(location)).resolve() if location else path.resolve()
        try:
            destination.relative_to(root)
        except ValueError:
            broken.append(BrokenLink(path, line, target, "points outside the repository"))
            continue
        if not destination.exists():
            broken.append(BrokenLink(path, line, target, "no such file or directory"))
            continue
        if anchor and destination.suffix.lower() == ".md":
            if destination not in anchor_cache:
                anchor_cache[destination] = heading_anchors(destination.read_text(encoding="utf-8"))
            if unquote(anchor).lower() not in anchor_cache[destination]:
                broken.append(BrokenLink(path, line, target, "no such heading"))
    return broken


def tracked_markdown(root: Path) -> list[Path]:
    """Markdown files under version control, so ignored caches and dependencies are skipped."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    return sorted(root / name for name in listing.split("\0") if name)


def check_repository(root: Path) -> list[BrokenLink]:
    root = root.resolve()
    anchor_cache: dict[Path, set[str]] = {}
    broken: list[BrokenLink] = []
    for path in tracked_markdown(root):
        broken.extend(check_file(path, root, anchor_cache))
    return broken


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    args = parser.parse_args()
    root = args.root.resolve()
    broken = check_repository(root)
    for link in broken:
        print(link.render(root), file=sys.stderr)
    if broken:
        print(f"{len(broken)} broken documentation link(s).", file=sys.stderr)
        return 1
    print("Documentation links resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
