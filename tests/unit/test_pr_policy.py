from pathlib import Path

WORKFLOW = Path(".github/workflows/pr-policy.yml")


def test_pr_policy_allows_security_and_operations_branches() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "security/*" in text
    assert "ops/*" in text
    assert "security/, ops/" in text
