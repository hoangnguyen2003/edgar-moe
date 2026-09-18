"""Write a redacted, machine-readable context record for a failed cycle."""

from __future__ import annotations

import json
import os
from pathlib import Path

from edgar_moe.forward.failure_context import build_failure_context


def main() -> int:
    destination = Path(
        os.environ.get(
            "EDGAR_MOE_FAILURE_CONTEXT_PATH",
            "data/forward/diagnostics/forward-failure-context.json",
        )
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(build_failure_context(os.environ), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
