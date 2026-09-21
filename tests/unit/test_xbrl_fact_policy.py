from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from edgar_moe.features.dataset import (
    DAYS_PER_YEAR,
    ResearchDataset,
    dataset_xbrl_fact_policy,
    extract_company_facts,
    select_fundamentals,
)

Q2_END = "2024-06-30"
FILED = datetime(2024, 8, 1, 20, 0, tzinfo=UTC)
CUTOFF = datetime(2024, 8, 1, 21, 0, tzinfo=UTC)
REPORT_PERIOD = date(2024, 6, 30)
ACCESSION = "0000000001-24-000002"


def _row(
    value: float,
    end: str,
    *,
    start: str | None = None,
    accn: str = ACCESSION,
    filed: str = "2024-08-01",
) -> dict:
    # Accessions outside the acceptance map become available the day after `filed`.
    row = {"val": value, "end": end, "accn": accn, "form": "10-Q", "filed": filed}
    if start is not None:
        row["start"] = start
    return row


def _facts(concepts: dict[str, list[dict]]) -> dict:
    payload = {
        "facts": {
            "us-gaap": {concept: {"units": {"USD": rows}} for concept, rows in concepts.items()}
        }
    }
    return extract_company_facts(payload, {ACCESSION: FILED})


def test_v2_prefers_the_quarter_and_annualizes_flows() -> None:
    # A Q2 10-Q tags both the 3-month quarter and the 6-month year-to-date total.
    facts = _facts(
        {
            "NetIncomeLoss": [
                _row(25.0, Q2_END, start="2024-01-01"),
                _row(10.0, Q2_END, start="2024-04-01"),
            ]
        }
    )

    legacy, _ = select_fundamentals(facts, CUTOFF, REPORT_PERIOD, policy="legacy_v1")
    current, available_at = select_fundamentals(
        facts, CUTOFF, REPORT_PERIOD, policy="duration_aware_v2"
    )

    # Legacy v1 keeps whichever tied fact comes first: here the year-to-date sum.
    assert legacy["NetIncomeLoss"] == 25.0
    assert current["NetIncomeLoss"] == pytest.approx(10.0 * DAYS_PER_YEAR / 91)
    assert available_at == FILED


def test_v2_quarter_and_year_to_date_facts_share_one_annual_scale() -> None:
    quarter = _facts({"NetIncomeLoss": [_row(10.0, Q2_END, start="2024-04-01")]})
    year_to_date = _facts({"NetIncomeLoss": [_row(20.0, Q2_END, start="2024-01-01")]})

    from_quarter, _ = select_fundamentals(
        quarter, CUTOFF, REPORT_PERIOD, policy="duration_aware_v2"
    )
    from_ytd, _ = select_fundamentals(
        year_to_date, CUTOFF, REPORT_PERIOD, policy="duration_aware_v2"
    )

    assert from_quarter["NetIncomeLoss"] == pytest.approx(from_ytd["NetIncomeLoss"], rel=0.01)


def test_v2_ignores_stale_concepts_and_uses_the_current_revenue_alias() -> None:
    facts = _facts(
        {
            # A tag the issuer stopped using in 2018, as in the APH/TMO/TT cases.
            "Revenues": [
                _row(
                    3.85e9,
                    "2018-06-30",
                    start="2018-04-01",
                    accn="0000000001-18-000002",
                    filed="2018-08-01",
                )
            ],
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                _row(16.38e9, Q2_END, start="2024-04-01")
            ],
        }
    )

    legacy, _ = select_fundamentals(facts, CUTOFF, REPORT_PERIOD, policy="legacy_v1")
    current, _ = select_fundamentals(facts, CUTOFF, REPORT_PERIOD, policy="duration_aware_v2")

    assert legacy["Revenues"] == 3.85e9
    assert current["Revenues"] == pytest.approx(16.38e9 * DAYS_PER_YEAR / 91)


def test_v2_prefers_total_revenue_when_both_aliases_cover_the_period() -> None:
    facts = _facts(
        {
            "Revenues": [_row(12.0, Q2_END, start="2024-04-01")],
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                _row(11.0, Q2_END, start="2024-04-01")
            ],
        }
    )

    current, _ = select_fundamentals(facts, CUTOFF, REPORT_PERIOD, policy="duration_aware_v2")

    assert current["Revenues"] == pytest.approx(12.0 * DAYS_PER_YEAR / 91)


def test_v2_drops_values_older_than_the_staleness_bound() -> None:
    facts = _facts(
        {
            "Assets": [_row(100.0, "2023-12-31", accn="0000000001-24-000001", filed="2024-02-15")],
            "NetIncomeLoss": [
                _row(
                    5.0,
                    "2023-12-31",
                    start="2023-10-01",
                    accn="0000000001-24-000001",
                    filed="2024-02-15",
                )
            ],
        }
    )

    legacy, _ = select_fundamentals(facts, CUTOFF, REPORT_PERIOD, policy="legacy_v1")
    current, available_at = select_fundamentals(
        facts, CUTOFF, REPORT_PERIOD, policy="duration_aware_v2"
    )

    assert legacy == {"Assets": 100.0, "NetIncomeLoss": 5.0}
    assert current == {}
    assert available_at is None


def test_v2_requires_a_reporting_period_for_flows_but_not_instants() -> None:
    facts = _facts(
        {
            "NetIncomeLoss": [_row(7.0, Q2_END)],
            "Assets": [_row(100.0, Q2_END)],
        }
    )

    current, _ = select_fundamentals(facts, CUTOFF, REPORT_PERIOD, policy="duration_aware_v2")

    assert current == {"Assets": 100.0}


def test_both_policies_respect_the_point_in_time_cutoff() -> None:
    facts = _facts({"Assets": [_row(100.0, Q2_END)]})
    before_acceptance = datetime(2024, 8, 1, 19, 0, tzinfo=UTC)

    for policy in ("legacy_v1", "duration_aware_v2"):
        values, available_at = select_fundamentals(
            facts, before_acceptance, REPORT_PERIOD, policy=policy
        )
        assert values == {} and available_at is None


def test_malformed_start_dates_do_not_change_legacy_rows() -> None:
    facts = _facts({"NetIncomeLoss": [_row(3.0, Q2_END, start="not-a-date")]})

    assert facts["NetIncomeLoss"][0].start is None
    legacy, _ = select_fundamentals(facts, CUTOFF, REPORT_PERIOD, policy="legacy_v1")
    assert legacy == {"NetIncomeLoss": 3.0}


def test_unknown_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported XBRL fact policy"):
        select_fundamentals({}, CUTOFF, REPORT_PERIOD, policy="latest")  # type: ignore[arg-type]


def _dataset(provenance: dict[str, str]) -> ResearchDataset:
    empty = np.zeros((0, 1), dtype=np.float32)
    return ResearchDataset(
        dataset_id="dataset",
        as_of=date(2026, 7, 31),
        events=pd.DataFrame(),
        modalities={"text": empty, "fundamental": empty, "market": empty},
        regime=empty,
        target=np.zeros(0, dtype=np.float32),
        daily_returns=pd.DataFrame(),
        availability=pd.DataFrame(),
        feature_names={},
        attrition={},
        source_manifest_hash="a" * 64,
        provenance=provenance,
    )


def test_datasets_that_predate_the_policy_are_legacy_v1() -> None:
    assert dataset_xbrl_fact_policy(_dataset({"text_encoder": "finbert"})) == "legacy_v1"
    assert (
        dataset_xbrl_fact_policy(_dataset({"xbrl_fact_policy": "duration_aware_v2"}))
        == "duration_aware_v2"
    )
