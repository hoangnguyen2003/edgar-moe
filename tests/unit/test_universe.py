from datetime import date

import pytest

from edgar_moe.data.refresh import UniverseMember
from edgar_moe.data.universe import is_probable_fund, screen_liquid_universe


def test_screen_liquid_universe_ranks_and_audits_exclusions() -> None:
    members = [
        UniverseMember(cik="1", symbol="HIGH"),
        UniverseMember(cik="2", symbol="LOW"),
        UniverseMember(cik="3", symbol="SHORT"),
        UniverseMember(cik="4", symbol="ETF", company_name="Example ETF Trust"),
    ]
    bars = {
        "HIGH": [{"t": f"2026-01-{day:02d}", "c": 100.0, "v": 1_000.0} for day in range(1, 6)],
        "LOW": [{"t": f"2026-01-{day:02d}", "c": 10.0, "v": 100.0} for day in range(1, 6)],
        "SHORT": [{"t": "2026-01-01", "c": 50.0, "v": 1_000.0}],
    }

    selected, audit = screen_liquid_universe(
        members,
        bars,
        candidate_count=1,
        minimum_sessions=3,
        minimum_price=5.0,
    )

    assert selected[0]["symbol"] == "HIGH"
    assert selected[0]["screen_liquidity_rank"] == 1
    assert audit == {
        "fund_or_etp": 1,
        "insufficient_sessions": 1,
        "below_candidate_cutoff": 1,
        "selected": 1,
    }


def test_probable_fund_filter_does_not_exclude_operating_trusts() -> None:
    assert is_probable_fund("SPDR S&P 500 ETF TRUST")
    assert is_probable_fund("INVESCO QQQ TRUST, SERIES 1")
    assert not is_probable_fund("Digital Realty Trust, Inc.")


def test_screen_rejects_bars_after_declared_cutoff() -> None:
    with pytest.raises(ValueError, match="beyond the declared cutoff"):
        screen_liquid_universe(
            [UniverseMember(cik="1", symbol="AAA")],
            {"AAA": [{"t": "2023-01-02T00:00:00Z", "c": 10, "v": 100}]},
            candidate_count=1,
            minimum_sessions=1,
            minimum_price=1,
            as_of=date(2022, 12, 31),
        )
