from pathlib import Path

WORKFLOW = Path(".github/workflows/pr-policy.yml")
WORKFLOW_ROOT = Path(".github/workflows")


def test_pr_policy_allows_security_and_operations_branches() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "security/*" in text
    assert "ops/*" in text
    assert "security/, ops/" in text


def test_all_uv_sync_workflows_require_the_committed_lockfile() -> None:
    offenders = [
        f"{path}:{line_number}: {line.strip()}"
        for path in sorted(WORKFLOW_ROOT.glob("*.yml"))
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "uv sync" in line and "--locked" not in line
    ]

    assert offenders == []
