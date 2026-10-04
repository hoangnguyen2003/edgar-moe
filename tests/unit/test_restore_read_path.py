import hashlib
import json
from unittest.mock import MagicMock

import pytest

from scripts import probe_restore_read_path as probe


def expected_counts():
    expected = dict.fromkeys(probe.REGISTRY_TABLES, 0)
    expected.update(forward_models=1, forward_runs=2, forward_forecasts=3, forward_labels=1)
    return expected


def registry_fixture(health="degraded"):
    registry = MagicMock()
    registry.status.return_value = {
        "configured": True,
        "health_status": health,
        "model_count": 1,
        "run_count": 2,
        "forecast_count": 3,
        "matured_count": 1,
        "health_message": "private-provider-text",
        "model_id": "private-model",
    }
    registry.list_forecasts.return_value = {
        "total": 3,
        "items": [{"forecast_id": "private-forecast", "score": 0.987}],
    }
    registry.performance.return_value = {
        "forecast_count": 3,
        "matured_count": 1,
        "returns": "private-values",
    }
    return registry


@pytest.mark.parametrize("health", ["ok", "warning", "degraded"])
def test_read_availability_preserves_health_without_rewriting_or_masking_it(health):
    registry = registry_fixture(health)
    report = probe.probe_registry(registry, expected_counts())
    assert report["status"] == "passed"
    assert report["read_path_available"] is True
    assert report["aggregate_counts_match"] is True
    assert report["health_status"] == health
    assert report["forward_health_ok"] is (health == "ok")
    assert registry.status.return_value["health_status"] == health
    registry.list_forecasts.assert_called_once_with(limit=1)
    assert "private-" not in json.dumps(report)
    assert "score" not in json.dumps(report)


@pytest.mark.parametrize("value", [True, -1, "1", None, 1.0])
def test_expected_counts_require_nonnegative_integers(value):
    expected = expected_counts()
    expected["forward_runs"] = value
    with pytest.raises(probe.RestoreReadPathError, match="^invalid_expected_registry_counts$"):
        probe.parse_expected_counts(expected)


@pytest.mark.parametrize("payload", [None, [], {}, {"unrelated": 1}])
def test_expected_counts_require_exact_registry_table_set(payload):
    with pytest.raises(probe.RestoreReadPathError, match="^invalid_expected_registry_counts$"):
        probe.parse_expected_counts(payload)


@pytest.mark.parametrize(
    "method,field",
    [
        ("status", "model_count"),
        ("status", "run_count"),
        ("status", "forecast_count"),
        ("status", "matured_count"),
        ("list_forecasts", "total"),
        ("performance", "forecast_count"),
        ("performance", "matured_count"),
    ],
)
@pytest.mark.parametrize("bad_count", [999, True, "3", None])
def test_wrong_or_malformed_aggregate_counts_still_fail(method, field, bad_count):
    registry = registry_fixture()
    getattr(registry, method).return_value[field] = bad_count
    with pytest.raises(probe.RestoreReadPathError, match="^aggregate_count_mismatch$"):
        probe.probe_registry(registry, expected_counts())


@pytest.mark.parametrize("health", [None, [], "unsupported", True])
def test_unknown_health_is_not_silently_accepted(health):
    with pytest.raises(probe.RestoreReadPathError, match="^invalid_registry_status$"):
        probe.probe_registry(registry_fixture(health), expected_counts())


def test_unconfigured_registry_fails():
    registry = registry_fixture()
    registry.status.return_value["configured"] = False
    with pytest.raises(probe.RestoreReadPathError, match="^invalid_registry_status$"):
        probe.probe_registry(registry, expected_counts())


@pytest.mark.parametrize("items", [[], None, "private-items", [{}, {}]])
def test_failed_forecast_sample_read_fails(items):
    registry = registry_fixture()
    registry.list_forecasts.return_value["items"] = items
    with pytest.raises(probe.RestoreReadPathError, match="^forecast_sample_mismatch$"):
        probe.probe_registry(registry, expected_counts())


def test_probe_uses_explicit_read_only_bounded_connections_and_disposes(monkeypatch):
    database = MagicMock()
    create = MagicMock(return_value=database)
    monkeypatch.setattr(probe, "RegistryDatabase", create)
    monkeypatch.setattr(probe, "ForwardRegistry", MagicMock(return_value=registry_fixture()))
    url = "postgresql://reader:synthetic-secret@fixture.invalid/fixture?sslmode=verify-full"
    assert probe.probe(url, expected_counts())["status"] == "passed"
    create.assert_called_once_with(
        url,
        read_only=True,
        statement_timeout_ms=10_000,
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    database.dispose.assert_called_once_with()
    database.create_schema.assert_not_called()


def test_probe_disposes_even_when_read_fails(monkeypatch):
    database = MagicMock()
    registry = registry_fixture()
    registry.status.side_effect = RuntimeError("private-error")
    monkeypatch.setattr(probe, "RegistryDatabase", MagicMock(return_value=database))
    monkeypatch.setattr(probe, "ForwardRegistry", MagicMock(return_value=registry))
    with pytest.raises(RuntimeError):
        probe.probe("postgresql://reader@fixture.invalid/fixture", expected_counts())
    database.dispose.assert_called_once_with()


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("postgresql://reader:private-secret@private-host/private-db"),
        probe.RestoreReadPathError("aggregate_count_mismatch"),
    ],
)
def test_cli_always_retains_redacted_failure_json(tmp_path, monkeypatch, capsys, failure):
    counts = tmp_path / "counts.json"
    counts.write_text(json.dumps(expected_counts()))
    monkeypatch.setattr(
        "sys.argv", ["probe_restore_read_path.py", "--expected-counts", str(counts)]
    )
    monkeypatch.setattr(probe, "probe", MagicMock(side_effect=failure))
    assert probe.main() == 1
    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert report["status"] == "failed"
    assert report["read_path_available"] is False
    assert "private-" not in raw
    assert report["reason"] in {"read_probe_failed", "aggregate_count_mismatch"}


def test_cli_hashes_exact_baseline_bytes_and_preserves_degraded_health(
    tmp_path, monkeypatch, capsys
):
    counts = tmp_path / "counts.json"
    raw_counts = json.dumps(expected_counts()).encode()
    counts.write_bytes(raw_counts)
    monkeypatch.setattr(
        "sys.argv", ["probe_restore_read_path.py", "--expected-counts", str(counts)]
    )
    monkeypatch.setattr(
        probe,
        "probe",
        MagicMock(return_value=probe.probe_registry(registry_fixture(), expected_counts())),
    )
    assert probe.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["health_status"] == "degraded"
    assert report["forward_health_ok"] is False
    assert report["expected_counts_sha256"] == hashlib.sha256(raw_counts).hexdigest()
    assert "not_forward_health_or_source_use_approval" in report["limitations"]


def test_followup_output_is_private_and_never_overwritten(tmp_path, monkeypatch, capsys):
    counts = tmp_path / "counts.json"
    counts.write_text(json.dumps(expected_counts()))
    output = tmp_path / "read-followup.json"
    monkeypatch.setattr(
        "sys.argv", ["probe", "--expected-counts", str(counts), "--output", str(output)]
    )
    monkeypatch.setattr(
        probe,
        "probe",
        MagicMock(return_value=probe.probe_registry(registry_fixture(), expected_counts())),
    )
    assert probe.main() == 0
    original = output.read_bytes()
    assert output.stat().st_mode & 0o777 == 0o600
    assert probe.main() == 2
    assert output.read_bytes() == original
    assert "report_write_failed" in capsys.readouterr().out
