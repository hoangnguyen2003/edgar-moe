from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path("scripts").resolve()))
spec = importlib.util.spec_from_file_location(
    "private_scheduler_cycle", "scripts/run_private_scheduler_cycle.py"
)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize("unsafe", ["../escape", "filing-cache/link"])
def test_seed_restoration_rejects_traversal_and_symlinks(tmp_path, unsafe):
    source = tmp_path / "source.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        member = tarfile.TarInfo(unsafe)
        if unsafe.endswith("link"):
            member.type = tarfile.SYMTYPE
            member.linkname = "/tmp/escape"
        archive.addfile(member, io.BytesIO())
    metadata = {
        "key": "private-seed",
        "size_bytes": source.stat().st_size,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    store = SimpleNamespace(
        bucket="test",
        client=SimpleNamespace(
            download_file=lambda bucket, key, target: shutil.copyfile(source, target)
        ),
    )
    destination = tmp_path / "restored"
    destination.mkdir()
    with pytest.raises(ValueError, match="Unsafe"):
        runner.restore_seed(store, {"private_cache_seed": metadata}, destination)
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("forecasts", [0, 1])
def test_real_recording_is_required_and_existing_rows_are_preserved(
    monkeypatch, tmp_path, forecasts
):
    calls = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 5, 8, 0, tzinfo=UTC)

    class Database:
        def __init__(self, *args, **kwargs):
            pass

        def dispose(self):
            calls.append("dispose")

    class Store:
        def __init__(self, **kwargs):
            self.bucket = "private"
            self.client = SimpleNamespace(put_object=lambda **kw: calls.append(kw))

        def put_file(self, path, **kwargs):
            calls.append("private_archive")
            assert path.is_file()
            return SimpleNamespace(sha256="0" * 64)

    class Workflow:
        def __init__(self, *args):
            pass

        def forecast(self, *args, **kwargs):
            calls.append("forecast")
            return SimpleNamespace(run_id="run", counts={"forecasts_inserted": forecasts})

        def settle(self, *args, **kwargs):
            return SimpleNamespace(run_id="settlement")

    async def refresh(*args):
        calls.append("refresh")
        return tmp_path

    record = {
        "private_target_fingerprints": {"registry": "test", "storage": "test"},
        "cached_finbert_revision": "test",
    }
    settings = SimpleNamespace(
        edgar_moe_registry_database_url="unused",
        edgar_moe_r2_endpoint_url="unused",
        edgar_moe_r2_bucket="unused",
        edgar_moe_r2_access_key_id="unused",
        edgar_moe_r2_secret_access_key="unused",
    )
    config = SimpleNamespace(
        features=SimpleNamespace(
            embedding_model="test", embedding_chunk_tokens=510, embedding_max_chunks=4
        )
    )
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    for name, value in {
        "datetime": Clock,
        "RuntimeSettings": lambda **kw: settings,
        "target_fingerprints": lambda s: record["private_target_fingerprints"],
        "compare_targets": lambda *a: {"status": "matches_private_reference"},
        "validate_frozen_runtime": lambda *a: [],
        "ResearchConfig": SimpleNamespace(from_yaml=lambda *a: config),
        "R2ArtifactStore": Store,
        "RegistryDatabase": Database,
        "restore_seed": lambda *a: None,
        "source_cutoff": lambda *a: "2026-11-05",
        "refresh": refresh,
        "FinBertEmbedder": lambda **kw: SimpleNamespace(model_revision="test"),
        "build_research_dataset": lambda *a, **kw: SimpleNamespace(save=lambda *a: tmp_path),
        "target_nyse_open": lambda now: Clock(2026, 11, 5, 14, 30, tzinfo=UTC),
        "FrozenModelSpec": SimpleNamespace(from_yaml=lambda *a: None),
        "ForwardWorkflow": Workflow,
        "ForwardRegistry": lambda *a, **kw: SimpleNamespace(
            list_quality_checks=lambda **kw: [
                {
                    "run_id": "run",
                    "name": "pre_open_schedule_margin",
                    "status": "passed",
                    "observed_value": 23400,
                }
            ]
        ),
    }.items():
        monkeypatch.setattr(runner, name, value)
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *a, **kw: "revision")
    states = iter(
        [
            {"forward_runs": {"existing": "unchanged"}},
            {"forward_runs": {"existing": "unchanged", "new": "appended"}},
        ]
    )
    monkeypatch.setattr(runner, "snapshot", lambda *a: next(states))
    output = tmp_path / "run"
    output.mkdir()
    result = runner.run(tmp_path, record, Clock(2026, 11, 5, 7, 17, tzinfo=UTC), output)
    assert result["forecast_margin_proven"] is bool(forecasts)
    assert result["status"] == ("recorded" if forecasts else "no_eligible_forecasts")
    assert result["issue_complete"] is False
    assert result["existing_records_unchanged"] is True
    assert calls[0]["IfNoneMatch"] == "*"
    assert calls[0]["Key"].endswith("2026-11-05.claim.json")
    assert "private_archive" in calls
    assert json.loads((output / "registry-appended.json").read_text()) == {"forward_runs": 1}


def test_upload_budget_covers_all_object_methods_and_refuses_before_writes(tmp_path):
    from unittest.mock import Mock

    underlying = Mock(client=Mock(), bucket="synthetic")
    store = runner.BudgetedStore(underlying, limit=10)
    path = tmp_path / "synthetic"
    path.write_bytes(b"123456")
    store.put_file(path)
    store.put_bytes(b"1234", logical_name="synthetic")
    assert store.bytes_reserved == 10
    with pytest.raises(ValueError, match="budget exceeded"):
        store.put_bytes(b"x", logical_name="overflow")
    with pytest.raises(ValueError, match="budget exceeded"):
        store.put_file(path)
    assert underlying.put_file.call_count == underlying.put_bytes.call_count == 1
