from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "check_doc_links.py"
_SPEC = importlib.util.spec_from_file_location("check_doc_links", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


@pytest.mark.parametrize(
    ("heading", "slug"),
    [
        ("Research contract", "research-contract"),
        ("8. Verification and traceability", "8-verification-and-traceability"),
        ("Architecture baseline — September 18, 2026", "architecture-baseline--september-18-2026"),
        (
            "Proposed service objectives — not measured commitments",
            "proposed-service-objectives--not-measured-commitments",
        ),
        ("Known `v1` limitations", "known-v1-limitations"),
        ("See [ADR 0016](adr/0016.md) now", "see-adr-0016-now"),
        ("snake_case & more!", "snake_case--more"),
    ],
)
def test_github_slugs_match_rendered_anchors(heading: str, slug: str) -> None:
    assert _MODULE.github_slug(heading) == slug


def test_duplicate_headings_get_numbered_anchors() -> None:
    anchors = _MODULE.heading_anchors("# Setup\n\n## Setup\n\n```\n# Not a heading\n```\n")

    assert anchors == {"setup", "setup-1"}


def test_links_in_code_are_ignored() -> None:
    markdown = "[a](a.md) and `[b](b.md)`\n\n```bash\n[c](c.md)\n```\n[ref]: d.md\n"

    assert _MODULE.link_targets(markdown) == [(1, "a.md"), (6, "d.md")]


def test_check_file_reports_missing_paths_headings_and_escapes(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n\n## Steps\n", encoding="utf-8")
    source = tmp_path / "README.md"
    source.write_text(
        "[ok](docs/guide.md#steps) [dir](docs/) [self](#intro) [web](https://example.test)\n"
        "[gone](docs/missing.md) [heading](docs/guide.md#nope) [out](../outside.md)\n"
        "# Intro\n",
        encoding="utf-8",
    )

    broken = _MODULE.check_file(source, tmp_path.resolve(), {})

    assert [(link.line, link.target, link.reason) for link in broken] == [
        (2, "docs/missing.md", "no such file or directory"),
        (2, "docs/guide.md#nope", "no such heading"),
        (2, "../outside.md", "points outside the repository"),
    ]


def test_repository_documentation_links_resolve() -> None:
    root = Path(__file__).parents[2]

    broken = _MODULE.check_repository(root)

    assert [link.render(root.resolve()) for link in broken] == []
