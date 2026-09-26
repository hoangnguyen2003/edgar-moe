from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest

from edgar_moe.data import screen_audit
from edgar_moe.data.storage import sha256_file


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path, Path]:
    source = tmp_path / "source.csv"
    screened = tmp_path / "screened.csv"
    source.write_text("cik,symbol\n1,AAA\n2,BBB\n", encoding="utf-8")
    screened.write_text("cik,symbol\n1,AAA\n", encoding="utf-8")
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "manifest.json").write_text("{}", encoding="utf-8")
    (checkpoint / "requested-universe.json").write_bytes(
        orjson.dumps(
            [
                {
                    "cik": "0000000001",
                    "symbol": "AAA",
                    "security_id": None,
                    "company_name": None,
                    "exchange": None,
                    "industry_code": None,
                }
            ]
        )
    )
    monkeypatch.setattr(
        screen_audit,
        "verify_authenticated_bundle",
        lambda _: SimpleNamespace(
            configuration={
                "assets": {"requested_universe": "requested-universe.json"},
                "as_of": "2025-12-31",
            }
        ),
    )
    audit = tmp_path / "screen.json"
    audit.write_bytes(
        orjson.dumps(
            {
                "schema_version": 2,
                "as_of": "2022-12-30",
                "lookback_start": "2022-09-01",
                "source_sha256": sha256_file(source),
                "screened_universe_sha256": sha256_file(screened),
                "source_members": 2,
                "exclusions": {"selected": 1},
            }
        )
    )
    return audit, source, screened, checkpoint


def test_screen_trace_binds_screen_to_checkpoint_without_overclaiming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit, source, screened, checkpoint = _fixture(tmp_path, monkeypatch)
    result = screen_audit.verify_screen_trace(
        audit, source, screened, checkpoint, first_validation_start=date(2023, 1, 1)
    )
    assert result["status"] == "screen_trace_verified_upstream_membership_unverified"
    assert result["candidate_count"] == 1
    assert "retrospective" in result["limitation"]


@pytest.mark.parametrize(
    "mutation", ["late_screen", "tampered_csv", "wrong_checkpoint", "old_audit"]
)
def test_screen_trace_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    audit, source, screened, checkpoint = _fixture(tmp_path, monkeypatch)
    if mutation == "tampered_csv":
        screened.write_text("cik,symbol\n2,BBB\n", encoding="utf-8")
    elif mutation == "wrong_checkpoint":
        (checkpoint / "requested-universe.json").write_bytes(orjson.dumps([]))
    else:
        payload = orjson.loads(audit.read_bytes())
        if mutation == "late_screen":
            payload["as_of"] = "2026-07-31"
        else:
            payload.pop("schema_version")
        audit.write_bytes(orjson.dumps(payload))
    with pytest.raises(ValueError):
        screen_audit.verify_screen_trace(
            audit, source, screened, checkpoint, first_validation_start=date(2023, 1, 1)
        )
