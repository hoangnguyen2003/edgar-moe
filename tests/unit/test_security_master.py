from edgar_moe.data.contracts import MappingStatus
from edgar_moe.data.security_master import (
    build_security_mapping,
    build_security_master,
    normalize_company_name,
    normalize_symbol,
)


def test_normalize_company_name_removes_suffixes() -> None:
    assert normalize_company_name("Example Holdings, Inc.") == "example"
    assert normalize_symbol("brk.b") == "BRKB"


def test_exact_ticker_and_name_is_confident() -> None:
    mapping = build_security_mapping(
        {"cik_str": 1234, "tickers": ["ACME"], "title": "Acme Corporation"},
        [
            {
                "id": "one",
                "symbol": "WRONG",
                "name": "Different Co",
                "exchange": "NYSE",
                "status": "active",
            },
            {
                "id": "two",
                "symbol": "ACME",
                "name": "Acme Inc",
                "exchange": "NASDAQ",
                "status": "active",
            },
        ],
    )
    assert mapping is not None
    assert mapping.security_id == "two"
    assert mapping.status is MappingStatus.CONFIDENT
    assert "exact_ticker" in mapping.evidence


def test_security_master_indexes_tickers_and_records_unmapped_issuers() -> None:
    payload = {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [
            [1234, "Acme Corporation", "ACME", "Nasdaq"],
            [5678, "Missing Corporation", "MISS", "NYSE"],
        ],
    }
    mappings = build_security_master(
        payload,
        [
            {
                "id": "asset-acme",
                "symbol": "ACME",
                "name": "Acme Inc",
                "exchange": "NASDAQ",
                "status": "active",
            }
        ],
    )

    assert len(mappings) == 2
    assert (
        next(item for item in mappings if item.cik.endswith("1234")).status
        is MappingStatus.CONFIDENT
    )
    missing = next(item for item in mappings if item.cik.endswith("5678"))
    assert missing.status is MappingStatus.EXCLUDED
    assert missing.evidence == ["no_alpaca_candidate"]
