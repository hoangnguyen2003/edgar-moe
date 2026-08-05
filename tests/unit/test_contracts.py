from datetime import UTC, date, datetime, timedelta

import pytest

from edgar_moe.data.contracts import (
    FeatureValue,
    FilingEvent,
    FilingForm,
    PointInTimeFeatureRow,
)


def event() -> FilingEvent:
    accepted = datetime(2026, 5, 1, 21, tzinfo=UTC)
    return FilingEvent(
        accession_number="0000000000-26-000001",
        cik="0000000001",
        security_id="asset-1",
        ticker_at_event="TEST",
        company_name="Test Corp",
        form=FilingForm.TEN_Q,
        accepted_at=accepted,
        report_period=date(2026, 3, 31),
        filing_url="https://www.sec.gov/example",
        entry_at=accepted + timedelta(hours=16),
        horizon_at=accepted + timedelta(days=30),
        industry_code="3571",
    )


def test_point_in_time_row_accepts_past_features() -> None:
    filing = event()
    row = PointInTimeFeatureRow(
        event=filing,
        numeric={
            "momentum": FeatureValue(
                name="momentum",
                value=0.1,
                available_at=filing.accepted_at - timedelta(hours=5),
                source="market",
            )
        },
        text_embedding=[0.1, 0.2],
        text_available_at=filing.accepted_at,
    )
    assert row.numeric["momentum"].value == 0.1


def test_point_in_time_row_rejects_future_features() -> None:
    filing = event()
    with pytest.raises(ValueError, match="available after prediction cutoff"):
        PointInTimeFeatureRow(
            event=filing,
            numeric={
                "future": FeatureValue(
                    name="future",
                    value=1.0,
                    available_at=filing.accepted_at + timedelta(seconds=1),
                    source="bad",
                )
            },
            text_embedding=[0.0],
            text_available_at=filing.accepted_at,
        )
