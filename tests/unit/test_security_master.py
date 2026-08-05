from edgar_moe.data.contracts import MappingStatus
from edgar_moe.data.security_master import build_security_mapping, normalize_company_name


def test_normalize_company_name_removes_suffixes() -> None:
    assert normalize_company_name("Example Holdings, Inc.") == "example"


def test_exact_ticker_and_name_is_confident() -> None:
    mapping = build_security_mapping(
        {"cik_str": 1234, "tickers": ["ACME"], "title": "Acme Corporation"},
        [
            {"id": "one", "symbol": "WRONG", "name": "Different Co", "exchange": "NYSE", "status": "active"},
            {"id": "two", "symbol": "ACME", "name": "Acme Inc", "exchange": "NASDAQ", "status": "active"},
        ],
    )
    assert mapping is not None
    assert mapping.security_id == "two"
    assert mapping.status is MappingStatus.CONFIDENT
    assert "exact_ticker" in mapping.evidence
