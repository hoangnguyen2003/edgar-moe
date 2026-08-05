from pathlib import Path

from edgar_moe.data.sec import FilingParser


def test_parser_extracts_longest_real_sections() -> None:
    html = Path("tests/fixtures/filing.html").read_text(encoding="utf-8")
    sections = FilingParser().extract_sections(html)
    assert set(sections) == {"risk_factors", "management_discussion"}
    assert len(sections["risk_factors"].text) > 500
    assert "Revenue improved" in sections["management_discussion"].text
