"""Read-only terminal presentation of a verified answer; never changes its report."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from typing import Any

from .verification import verify_copilot_answer_report


def render_copilot_answer_text(report: Mapping[str, Any]) -> str:
    """Keep research limits and source digests visible without the JSON wrapper.

    Verification here checks the envelope, not answer factuality or source bytes.
    Escape terminal control and bidirectional-format characters for display only;
    the saved JSON remains the original, auditable report.
    """
    verify_copilot_answer_report(report)
    lines = [
        "Research-only evidence navigator",
        f"Mode: {report['model']} | Provider: {report['provider']}",
        f"Evidence: {report['evidence_status']} (citation status, not a factuality score)",
    ]
    if report["provider"] != "none":
        lines.append("Experimental model output; citations do not establish factual accuracy.")
    lines.extend(["", report["answer"], "", "Sources (report order):"])
    for index, citation in enumerate(report["citations"], start=1):
        lines.extend(
            [
                f"[{index}] {_terminal_safe(citation['label'], multiline=False)}",
                f"  Source: {_terminal_safe(citation['source'], multiline=False)}",
                f"  Evidence SHA-256: {citation['evidence_sha256']}",
                "  Fields: "
                + (
                    ", ".join(
                        _terminal_safe(field, multiline=False) for field in citation["fields"]
                    )
                    or "not specified"
                ),
            ]
        )
    if not report["citations"]:
        lines.append("None; this answer does not cite source evidence.")
    elif report["provider"] != "none":
        lines.append("Source numbering lists envelope citations; it does not remap model prose.")
    lines.extend(
        [
            "",
            f"Snapshot SHA-256: {report['frozen_identity']['sha256']}",
            "Digests identify bytes, not licensing approval or proof of performance.",
            report["disclaimer"],
        ]
    )
    return _terminal_safe("\n".join(lines))


def _terminal_safe(text: str, *, multiline: bool = True) -> str:
    return "".join(
        (f"\\u{ord(char):04x}" if ord(char) <= 0xFFFF else f"\\U{ord(char):08x}")
        if (not multiline or char not in "\n\t")
        and unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"}
        else char
        for char in text
    )
