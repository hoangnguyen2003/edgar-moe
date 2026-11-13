from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from edgar_moe.data.historical_identity import IdentityEvidenceError
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import enrich_historical_legacy_governance as supplement
from scripts.enrich_historical_legacy_governance import append_results, reference_plan


def baseline():
    return {
        "results": [
            {
                "snapshot_member_key": "a" * 64,
                "status": "pre_cutoff_SEC_identity_anchored",
                "anchor": {"opaque": "original-identity"},
            },
            {
                "snapshot_member_key": "b" * 64,
                "status": "ownership_identity_unresolved",
                "refusals": {"missing_voting_rights": 1},
            },
            {"snapshot_member_key": "c" * 64, "status": "periodic_anchor_unresolved"},
        ]
    }


def test_only_the_requested_unresolved_identity_is_appended_and_old_payload_is_unchanged():
    original = baseline()
    before = canonical_bytes(original)
    anchor = {"opaque": "separately-source-verified-identity"}
    result = append_results(original, {"b" * 64: anchor})
    assert canonical_bytes(original) == before
    assert len(result) == 3
    assert [r["snapshot_member_key"] for r in result] == [
        r["snapshot_member_key"] for r in original["results"]
    ]
    assert canonical_bytes(result[0]) == canonical_bytes(original["results"][0])
    assert canonical_bytes(result[2]) == canonical_bytes(original["results"][2])
    assert (
        result[1]["anchor"] == anchor and result[1]["status"] == "pre_cutoff_SEC_identity_anchored"
    )


@pytest.mark.parametrize("key", ["a" * 64, "c" * 64, "d" * 64])
def test_existing_identities_missing_periodic_evidence_and_foreign_members_cannot_be_overridden(
    key,
):
    original = baseline()
    before = deepcopy(original)
    with pytest.raises(IdentityEvidenceError):
        append_results(original, {key: {"opaque": "replacement"}})
    assert original == before


def test_duplicate_candidates_cannot_supply_a_changed_universe_denominator():
    original = baseline()
    original["results"].append(original["results"][0])
    with pytest.raises(IdentityEvidenceError, match="member_set_changed"):
        append_results(original, {})


def plan():
    return {
        "schema_version": 1,
        "scope": "private_legacy_governing_identity_only",
        "references": [{"snapshot_member_key": "b" * 64, "source_sha256": "e" * 64}],
    }


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"scope": "publication"},
        {"extra": "ignored"},
        {"references": []},
        {"references": [{"snapshot_member_key": "a" * 64, "source_sha256": "e" * 64}]},
        {"references": [{"snapshot_member_key": "b" * 64, "source_sha256": "invalid"}]},
        {
            "references": [
                {"snapshot_member_key": "b" * 64, "source_sha256": "e" * 64, "fallback": True}
            ]
        },
        {"references": plan()["references"] * 2},
    ],
)
def test_plan_cannot_change_scope_override_members_duplicate_sources_or_weaken_hashes(change):
    with pytest.raises(IdentityEvidenceError):
        reference_plan(canonical_bytes(plan() | change), {"b" * 64})


def test_bound_plan_preserves_exact_member_and_source_hash():
    assert reference_plan(canonical_bytes(plan()), {"b" * 64}) == plan()["references"]


def test_annual_and_current_registration_par_values_must_agree_before_source_qualification(
    monkeypatch,
):
    monkeypatch.setattr(supplement, "submissions", lambda *a: [])
    monkeypatch.setattr(
        supplement,
        "choose_filings",
        lambda rows, forms, cutoff, limit: [{"tag": "annual" if forms == {"10-K"} else "current"}],
    )
    monkeypatch.setattr(
        supplement,
        "periodic_anchor",
        lambda body, row, **k: {
            "security_title": f"Common Stock voting, ${'0.20' if row['tag'] == 'annual' else '0.10'} par value"
        },
    )
    retained = SimpleNamespace(filing=lambda row: b"registered")
    with pytest.raises(IdentityEvidenceError, match="annual_registration_class_conflict"):
        supplement.candidate(
            {"cik": "0000000001", "symbol_at_snapshot": "TEST"},
            "e" * 64,
            None,
            retained,
            -1,
            datetime(2022, 12, 28, tzinfo=UTC),
        )
