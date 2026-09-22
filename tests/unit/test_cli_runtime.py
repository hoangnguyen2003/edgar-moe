from __future__ import annotations

import subprocess
import sys
from textwrap import dedent

import pytest


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


@pytest.mark.parametrize(
    ("user_agent", "placeholder"),
    [
        ("", True),
        ("   ", True),
        ("EDGAR-MoE Research your-email@example.com", True),
        ("EDGAR-MoE Research research@EXAMPLE.COM", True),
        ("EDGAR-MoE Research ops@mail.example.org", True),
        ("EDGAR-MoE Research analyst@gmail.com", False),
        # Only the contact's domain counts, not the text around it.
        ("EDGAR-MoE example.com-mirror analyst@research.org", False),
        ("EDGAR-MoE Research analyst@example.com.evil.test", False),
    ],
)
def test_sec_user_agent_placeholder_check_reads_the_contact_domain(
    user_agent: str, placeholder: bool
) -> None:
    from edgar_moe.cli import _is_placeholder_user_agent

    assert _is_placeholder_user_agent(user_agent) is placeholder
