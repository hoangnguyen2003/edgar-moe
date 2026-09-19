from __future__ import annotations

import subprocess
import sys
from textwrap import dedent


def test_cli_module_import_does_not_require_research_stack() -> None:
    script = dedent(
        """
        import builtins

        blocked = {"cvxpy", "lightgbm", "numpy", "pandas", "sklearn", "torch", "transformers"}
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.partition(".")[0] in blocked:
                raise AssertionError(f"research-only import happened during CLI startup: {name}")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = guarded_import
        import edgar_moe.cli

        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
