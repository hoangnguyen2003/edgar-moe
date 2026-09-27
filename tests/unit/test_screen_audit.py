from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest
from typer.testing import CliRunner

import edgar_moe.cli as cli
from edgar_moe.cli import app
from edgar_moe.data import alpaca, screen_audit
from edgar_moe.data.storage import sha256_file


def _fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, Path, Path, Path]:
    source = tmp_path / "source.csv"
    screened = tmp_path / "screened.csv"
    source.write_text("cik,symbol\n1,AAA\n2,BBB\n", encoding="utf-8")
    screened.write_text("cik,symbol\n1,AAA\n", encoding="utf-8")
    review = tmp_path / "mapping-review.json"
    review.write_bytes(
        orjson.dumps(
            [
                {"cik": "1", "symbol": "AAA"},
                {"cik": "2", "symbol": "BBB"},
            ]
        )
    )
    capture = tmp_path / "source-capture.json"
    capture.write_bytes(
        orjson.dumps(
            {
                "schema_version": 1,
                "observed_at_utc": "2022-12-29T12:00:00Z",
                "source_universe_sha256": sha256_file(source),
                "mapping_review_sha256": sha256_file(review),
                "source_kind": "reviewed_sec_alpaca_mapping",
                "limitation": "Self-recorded observation; no historical membership or independent timestamp.",
            }
        )
    )
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
                "schema_version": 4,
                "purpose": "research_screen",
                "as_of": "2022-12-30",
                "lookback_start": "2022-09-01",
                "generated_at_utc": "2022-12-31T12:00:00Z",
                "source_sha256": sha256_file(source),
                "source_capture_sha256": sha256_file(capture),
                "screened_universe_sha256": sha256_file(screened),
                "source_members": 2,
                "exclusions": {"selected": 1},
            }
        )
    )
    return audit, source, screened, checkpoint, capture, review


def test_record_source_capture_hashes_reviewed_local_files(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("cik,symbol\n1,AAA\n", encoding="utf-8")
    review = tmp_path / "review.json"
    review.write_bytes(orjson.dumps([{"cik": "1", "symbol": "AAA"}]))
    capture = tmp_path / "capture.json"
    result = CliRunner().invoke(
        app,
        [
            "record-universe-capture",
            "--source",
            str(source),
            "--mapping-review",
            str(review),
            "--output",
            str(capture),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = orjson.loads(capture.read_bytes())
    assert payload["source_universe_sha256"] == sha256_file(source)
    assert payload["mapping_review_sha256"] == sha256_file(review)
    assert screen_audit.verify_source_capture(capture, source, review).tzinfo is not None

    review.write_bytes(b"[]")
    with pytest.raises(ValueError, match="absent from its review"):
        screen_audit.record_source_capture(source, review, capture)


def test_source_capture_rejects_ambiguous_or_mismatched_security_id(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("cik,symbol,security_id\n1,AAA,asset-old\n", encoding="utf-8")
    review = tmp_path / "review.json"
    capture = tmp_path / "capture.json"
    review.write_bytes(orjson.dumps([{"cik": "1", "symbol": "AAA", "security_id": "asset-new"}]))
    with pytest.raises(ValueError, match="security ID differs"):
        screen_audit.record_source_capture(source, review, capture)
    review.write_bytes(
        orjson.dumps(
            [
                {"cik": "1", "symbol": "AAA", "security_id": "asset-old"},
                {"cik": "1", "symbol": "AAA", "security_id": "asset-new"},
            ]
        )
    )
    with pytest.raises(ValueError, match="duplicate"):
        screen_audit.record_source_capture(source, review, capture)


def test_screen_observation_uses_new_york_cutoff_boundary() -> None:
    cutoff = date(2022, 12, 30)
    generated = datetime(2022, 12, 31, 12, tzinfo=UTC)
    screen_audit.verify_screen_observation_window(
        datetime(2022, 12, 31, 4, 59, tzinfo=UTC), cutoff, generated
    )
    with pytest.raises(ValueError, match="observed after the screen cutoff"):
        screen_audit.verify_screen_observation_window(
            datetime(2022, 12, 31, 5, tzinfo=UTC), cutoff, generated
        )


@pytest.mark.parametrize("change_during_fetch", [False, True])
@pytest.mark.parametrize("diagnostic", [False, True])
def test_screen_command_pins_inputs_before_fetching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change_during_fetch: bool,
    diagnostic: bool,
) -> None:
    source, review, capture = (
        tmp_path / name for name in ("source.csv", "review.json", "capture.json")
    )
    source.write_text("cik,symbol\n" + "".join(f"{i},A{i}\n" for i in range(1, 51)))
    review.write_bytes(orjson.dumps([{"cik": str(i), "symbol": f"A{i}"} for i in range(1, 51)]))
    screen_audit.record_source_capture(source, review, capture)
    if not diagnostic:
        # Synthetic historical capture only; a real CLI capture cannot be backdated.
        payload = orjson.loads(capture.read_bytes())
        payload["observed_at_utc"] = "2022-12-29T12:00:00Z"
        capture.write_bytes(orjson.dumps(payload))
    monkeypatch.setattr(
        cli,
        "runtime_settings",
        lambda: SimpleNamespace(alpaca_api_key="fixture", alpaca_api_secret="fixture"),
    )
    monkeypatch.setattr(cli, "_require_source_configuration", lambda *_args, **_kwargs: None)

    class FakeMarket:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def daily_bars(self, symbols, start, cutoff, feed):
            if change_during_fetch:
                review.write_bytes(b"[]")
            return {
                symbol: [{"t": f"2022-12-{day:02d}", "c": 10, "v": 100} for day in range(1, 21)]
                for symbol in symbols
            }

    monkeypatch.setattr(alpaca, "AlpacaDataClient", FakeMarket)
    output, audit = tmp_path / "screened.csv", tmp_path / "audit.json"
    result = CliRunner().invoke(
        app,
        [
            "screen-universe",
            "--source",
            str(source),
            "--source-capture",
            str(capture),
            "--mapping-review",
            str(review),
            "--output",
            str(output),
            "--audit-output",
            str(audit),
            "--as-of",
            "2022-12-30",
            "--candidate-count",
            "50",
            "--minimum-sessions",
            "20",
            *(["--allow-retrospective-diagnostic"] if diagnostic else []),
        ],
    )
    if change_during_fetch:
        assert result.exit_code != 0
        assert "changed while fetching" in str(result.exception)
        assert not output.exists() and not audit.exists()
    else:
        assert result.exit_code == 0, result.output
        payload = orjson.loads(audit.read_bytes())
        assert payload["schema_version"] == 4
        assert payload["purpose"] == (
            "retrospective_diagnostic" if diagnostic else "research_screen"
        )
        assert payload["source_capture_sha256"] == sha256_file(capture)
        assert payload["source_sha256"] == sha256_file(source)
        assert payload["generated_at_utc"].endswith("Z")


@pytest.mark.parametrize("cutoff", ["2022-12-30", "future"])
def test_screen_command_rejects_bad_chronology_before_provider_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cutoff: str
) -> None:
    _, source, _, _, capture, review = _fixture(tmp_path, monkeypatch)
    payload = orjson.loads(capture.read_bytes())
    payload["observed_at_utc"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    capture.write_bytes(orjson.dumps(payload))
    monkeypatch.setattr(cli, "runtime_settings", lambda: pytest.fail("credentials read too early"))
    monkeypatch.setattr(
        alpaca,
        "AlpacaDataClient",
        lambda *_args: pytest.fail("market provider called before chronology preflight"),
    )
    if cutoff == "future":
        cutoff = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()
    output, audit = tmp_path / "new-screen.csv", tmp_path / "new-audit.json"
    result = CliRunner().invoke(
        app,
        [
            "screen-universe",
            "--source",
            str(source),
            "--source-capture",
            str(capture),
            "--mapping-review",
            str(review),
            "--output",
            str(output),
            "--audit-output",
            str(audit),
            "--as-of",
            cutoff,
        ],
    )
    assert result.exit_code != 0
    expected = "screen cutoff" if cutoff == "2022-12-30" else "cutoff date completed"
    assert expected in str(result.exception)
    assert not output.exists() and not audit.exists()


def test_screen_trace_rejects_explicit_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit, source, screened, checkpoint, capture, review = _fixture(tmp_path, monkeypatch)
    payload = orjson.loads(audit.read_bytes())
    payload["purpose"] = "retrospective_diagnostic"
    audit.write_bytes(orjson.dumps(payload))
    with pytest.raises(ValueError, match="Retrospective diagnostics"):
        screen_audit.verify_screen_trace(
            audit,
            source,
            screened,
            checkpoint,
            first_validation_start=date(2023, 1, 1),
            source_capture_path=capture,
            mapping_review_path=review,
        )


def test_diagnostic_cannot_overwrite_default_research_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, source, _, _, capture, review = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "runtime_settings", lambda: pytest.fail("credentials read too early"))
    result = CliRunner().invoke(
        app,
        [
            "screen-universe",
            "--source",
            str(source),
            "--source-capture",
            str(capture),
            "--mapping-review",
            str(review),
            "--allow-retrospective-diagnostic",
        ],
    )
    assert result.exit_code != 0
    assert "Retrospective diagnostics require separate --output" in result.output


def test_screen_trace_binds_screen_to_checkpoint_without_overclaiming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit, source, screened, checkpoint, capture, review = _fixture(tmp_path, monkeypatch)
    result = screen_audit.verify_screen_trace(
        audit,
        source,
        screened,
        checkpoint,
        first_validation_start=date(2023, 1, 1),
        source_capture_path=capture,
        mapping_review_path=review,
    )
    assert result["status"] == "screen_trace_verified_upstream_membership_unverified"
    assert result["candidate_count"] == 1
    assert "no historical membership" in result["limitation"]
    assert result["source_capture_sha256"] == sha256_file(capture)


def test_screen_trace_binds_processed_dataset_and_rejects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit, source, screened, checkpoint, capture, review = _fixture(tmp_path, monkeypatch)
    processed = tmp_path / "research-fixture"
    processed.mkdir()
    assets = {
        "events": processed / "events.parquet",
        "daily_returns": processed / "daily-returns.parquet",
        "availability": processed / "availability.parquet",
        "features": processed / "features.npz",
    }
    for name, asset in assets.items():
        asset.write_bytes(f"synthetic {name}".encode())
    manifest = processed / "manifest.json"
    manifest.write_bytes(
        orjson.dumps(
            {
                "dataset_id": processed.name,
                "source_manifest_hash": sha256_file(checkpoint / "manifest.json"),
                "assets": {name: asset.name for name, asset in assets.items()},
                "hashes": {name: sha256_file(asset) for name, asset in assets.items()},
            }
        )
    )
    options = {
        "first_validation_start": date(2023, 1, 1),
        "source_capture_path": capture,
        "mapping_review_path": review,
        "processed_dataset_dir": processed,
    }
    result = screen_audit.verify_screen_trace(audit, source, screened, checkpoint, **options)
    assert result["processed_dataset_id"] == processed.name
    assert result["processed_manifest_sha256"] == sha256_file(manifest)

    assets["events"].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="asset hash mismatch"):
        screen_audit.verify_screen_trace(audit, source, screened, checkpoint, **options)
    assets["events"].write_bytes(b"synthetic events")
    payload = orjson.loads(manifest.read_bytes())
    payload["source_manifest_hash"] = "0" * 64
    manifest.write_bytes(orjson.dumps(payload))
    with pytest.raises(ValueError, match="not derived"):
        screen_audit.verify_screen_trace(audit, source, screened, checkpoint, **options)


def test_screen_trace_rejects_selected_mapping_outside_source_even_with_matching_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit, source, screened, checkpoint, capture, review = _fixture(tmp_path, monkeypatch)
    screened.write_text("cik,symbol\n1,RENAMED\n", encoding="utf-8")
    payload = orjson.loads(audit.read_bytes())
    payload["screened_universe_sha256"] = sha256_file(screened)
    audit.write_bytes(orjson.dumps(payload))
    with pytest.raises(ValueError, match="outside the captured source"):
        screen_audit.verify_screen_trace(
            audit,
            source,
            screened,
            checkpoint,
            first_validation_start=date(2023, 1, 1),
            source_capture_path=capture,
            mapping_review_path=review,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "late_screen",
        "tampered_csv",
        "wrong_checkpoint",
        "old_audit",
        "legacy_audit",
        "late_master",
        "late_generation",
        "early_generation",
        "tampered_review",
        "tampered_capture",
        "late_arriving_mapping",
        "renamed_security",
        "delisted_security_removed",
    ],
)
def test_screen_trace_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    audit, source, screened, checkpoint, capture, review = _fixture(tmp_path, monkeypatch)
    if mutation == "tampered_csv":
        screened.write_text("cik,symbol\n2,BBB\n", encoding="utf-8")
    elif mutation == "wrong_checkpoint":
        (checkpoint / "requested-universe.json").write_bytes(orjson.dumps([]))
    elif mutation == "tampered_review":
        review.write_bytes(orjson.dumps([]))
    elif mutation == "late_arriving_mapping":
        source.write_text("cik,symbol\n1,AAA\n2,BBB\n3,CCC\n", encoding="utf-8")
    elif mutation == "renamed_security":
        source.write_text("cik,symbol\n1,NEW\n2,BBB\n", encoding="utf-8")
    elif mutation == "delisted_security_removed":
        source.write_text("cik,symbol\n1,AAA\n", encoding="utf-8")
    elif mutation in {"late_master", "tampered_capture"}:
        payload = orjson.loads(capture.read_bytes())
        payload["observed_at_utc"] = (
            "2026-09-27T12:00:00Z" if mutation == "late_master" else "2022-12-28T12:00:00Z"
        )
        capture.write_bytes(orjson.dumps(payload))
        if mutation == "late_master":
            screen = orjson.loads(audit.read_bytes())
            screen["source_capture_sha256"] = sha256_file(capture)
            audit.write_bytes(orjson.dumps(screen))
    else:
        payload = orjson.loads(audit.read_bytes())
        if mutation == "late_screen":
            payload["as_of"] = "2026-07-31"
        elif mutation == "late_generation":
            payload["generated_at_utc"] = "2026-09-27T12:00:00Z"
        elif mutation == "early_generation":
            payload["generated_at_utc"] = "2022-12-31T02:00:00Z"  # Still Dec 30 in New York.
        elif mutation == "legacy_audit":
            payload["schema_version"] = 3
        else:
            payload.pop("schema_version")
        audit.write_bytes(orjson.dumps(payload))
    with pytest.raises(ValueError):
        screen_audit.verify_screen_trace(
            audit,
            source,
            screened,
            checkpoint,
            first_validation_start=date(2023, 1, 1),
            source_capture_path=capture,
            mapping_review_path=review,
        )
