from __future__ import annotations

import pytest

from edgar_moe.copilot.policy import (
    COPILOT_POLICY_ID,
    build_agent_identity,
    build_system_prompt,
    copilot_policy_sha256,
    normalize_copilot_profile,
    validate_agent_identity,
)


def test_review_profiles_are_bounded_and_content_addressed() -> None:
    assert normalize_copilot_profile(" ARCHITECT ") == "architect"
    assert "trust boundaries" in build_system_prompt("architect")
    assert "point-in-time" in build_system_prompt("quant")
    assert "scheduler" in build_system_prompt("operations")
    assert copilot_policy_sha256() == copilot_policy_sha256("research")
    assert copilot_policy_sha256("architect") != copilot_policy_sha256("operations")


def test_profile_identity_records_the_perspective_and_verifies() -> None:
    identity = build_agent_identity([], 4, 300.0, 512 * 1024, "architect").as_dict()

    assert identity == validate_agent_identity(identity)
    assert identity["policy_id"] == COPILOT_POLICY_ID
    assert identity["profile_id"] == "architect"
    assert identity["policy_sha256"] == copilot_policy_sha256("architect")


def test_invalid_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="copilot profile"):
        normalize_copilot_profile("trader")

    with pytest.raises(ValueError, match="profile_id"):
        validate_agent_identity(
            {
                "policy_id": COPILOT_POLICY_ID,
                "policy_sha256": copilot_policy_sha256(),
                "tool_contract_sha256": "a" * 64,
                "max_tool_calls": 4,
                "profile_id": "trader",
            }
        )


def test_legacy_identity_without_profile_remains_valid() -> None:
    identity = {
        "policy_id": COPILOT_POLICY_ID,
        "policy_sha256": copilot_policy_sha256(),
        "tool_contract_sha256": "b" * 64,
        "max_tool_calls": 4,
    }

    assert validate_agent_identity(identity) == identity
