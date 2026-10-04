from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from edgar_moe.forward.artifacts import ArtifactWriteError, LocalArtifactStore
from edgar_moe.forward.partial_write import record_partial_artifact
from scripts import rehearse_provider_partial_write as rehearsal


def configuration() -> dict[str, str]:
    return {
        "CONFIRM_ISOLATED_FAILURE": rehearsal.CONFIRM,
        "SOURCE_DATABASE_URL": "postgresql://source:secret@ep-production.example.neon.tech/neondb?sslmode=verify-full",
        "PROTECTED_R2_BUCKET": "production-bucket",
        "REHEARSAL_DATABASE_URL": f"postgresql://owner:secret@ep-isolated.example.neon.tech/{rehearsal.DATABASE}?sslmode=verify-full",
        "REHEARSAL_AUDITOR_DATABASE_URL": f"postgresql://reader:secret@ep-isolated.example.neon.tech/{rehearsal.DATABASE}?sslmode=verify-full",
        "REHEARSAL_R2_BUCKET": rehearsal.BUCKET,
        "REHEARSAL_R2_ENDPOINT_URL": "https://" + "a" * 32 + ".r2.cloudflarestorage.com",
        "REHEARSAL_R2_ACCESS_KEY_ID": "test-writer",
        "REHEARSAL_R2_SECRET_ACCESS_KEY": "writer-secret",
        "REHEARSAL_R2_AUDITOR_ACCESS_KEY_ID": "test-reader",
        "REHEARSAL_R2_AUDITOR_SECRET_ACCESS_KEY": "reader-secret",
    }


def test_exact_isolated_scope_is_valid() -> None:
    rehearsal.validate_scope(configuration())


@pytest.mark.parametrize(
    "mutation",
    [
        "confirmation",
        "missing",
        "source_host",
        "pooler_alias",
        "previous_copy",
        "reader_database",
        "reader_role",
        "production_bucket",
        "wrong_bucket",
        "http_endpoint",
        "custom_endpoint",
        "endpoint_userinfo",
        "endpoint_query",
        "endpoint_path",
        "owner_override",
        "same_key",
        "same_secret",
    ],
)
def test_preflight_rejects_unsafe_configuration_before_connections(monkeypatch, mutation):
    env = configuration()
    if mutation == "confirmation":
        env["CONFIRM_ISOLATED_FAILURE"] = "yes"
    elif mutation == "missing":
        env.pop("REHEARSAL_R2_AUDITOR_SECRET_ACCESS_KEY")
    elif mutation in {"source_host", "pooler_alias"}:
        env["SOURCE_DATABASE_URL"] = env["REHEARSAL_DATABASE_URL"].replace(
            "/" + rehearsal.DATABASE, "/production"
        )
        if mutation == "pooler_alias":
            env["SOURCE_DATABASE_URL"] = env["SOURCE_DATABASE_URL"].replace(
                "ep-isolated.", "ep-isolated-pooler."
            )
    elif mutation == "previous_copy":
        env["REHEARSAL_DATABASE_URL"] = env["REHEARSAL_DATABASE_URL"].replace(
            rehearsal.DATABASE, "edgar_restore_rehearsal_20261004_v3"
        )
    elif mutation == "reader_database":
        env["REHEARSAL_AUDITOR_DATABASE_URL"] = env["REHEARSAL_AUDITOR_DATABASE_URL"].replace(
            rehearsal.DATABASE, "other"
        )
    elif mutation == "reader_role":
        env["REHEARSAL_AUDITOR_DATABASE_URL"] = env["REHEARSAL_DATABASE_URL"]
    elif mutation == "production_bucket":
        env["PROTECTED_R2_BUCKET"] = rehearsal.BUCKET
    elif mutation == "wrong_bucket":
        env["REHEARSAL_R2_BUCKET"] = "other"
    elif mutation == "http_endpoint":
        env["REHEARSAL_R2_ENDPOINT_URL"] = env["REHEARSAL_R2_ENDPOINT_URL"].replace(
            "https:", "http:"
        )
    elif mutation == "custom_endpoint":
        env["REHEARSAL_R2_ENDPOINT_URL"] = "https://attacker.example"
    elif mutation == "endpoint_userinfo":
        env["REHEARSAL_R2_ENDPOINT_URL"] = env["REHEARSAL_R2_ENDPOINT_URL"].replace(
            "https://", "https://secret@"
        )
    elif mutation == "endpoint_query":
        env["REHEARSAL_R2_ENDPOINT_URL"] += "?secret=value"
    elif mutation == "endpoint_path":
        env["REHEARSAL_R2_ENDPOINT_URL"] += "/bucket"
    elif mutation == "owner_override":
        env["REHEARSAL_DATABASE_URL"] += "&options=-csearch_path=production"
    elif mutation == "same_key":
        env["REHEARSAL_R2_AUDITOR_ACCESS_KEY_ID"] = env["REHEARSAL_R2_ACCESS_KEY_ID"]
    elif mutation == "same_secret":
        env["REHEARSAL_R2_AUDITOR_SECRET_ACCESS_KEY"] = env["REHEARSAL_R2_SECRET_ACCESS_KEY"]
    connection = Mock()
    monkeypatch.setattr(rehearsal, "observe", connection)
    with pytest.raises(ValueError):
        rehearsal.exercise(Path("unused"), env)
    connection.assert_not_called()


def test_child_environment_excludes_source_and_implicit_sdk_credentials(monkeypatch):
    for name in (
        "EDGAR_MOE_REGISTRY_DATABASE_URL",
        "SOURCE_DATABASE_URL",
        "PROTECTED_R2_BUCKET",
        "AWS_SECRET_ACCESS_KEY",
        "REHEARSAL_R2_SECRET_ACCESS_KEY",
        "AUDITOR_DATABASE_URL",
    ):
        monkeypatch.setenv(name, "secret")
    monkeypatch.setenv("PATH", "/trusted/bin")
    clean = rehearsal.child_environment()
    assert not any(
        key.startswith(("EDGAR_MOE_", "SOURCE_", "PROTECTED_", "AWS_", "REHEARSAL_", "AUDITOR_"))
        for key in clean
    )
    assert clean["PATH"] == "/trusted/bin"


def test_nonempty_database_stops_before_bucket_access_or_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(
        rehearsal, "observe", Mock(side_effect=ValueError("isolated_target_not_empty"))
    )
    remote = Mock()
    migration = Mock()
    monkeypatch.setattr(rehearsal, "store", remote)
    monkeypatch.setattr(rehearsal.subprocess, "run", migration)
    with pytest.raises(ValueError, match="isolated_target_not_empty"):
        rehearsal.exercise(tmp_path, configuration())
    remote.assert_not_called()
    migration.assert_not_called()


def test_registry_hash_uses_explicit_bounded_read_only_snapshot(monkeypatch):
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.execute.return_value.fetchall.return_value = []
    connect = Mock(return_value=connection)
    monkeypatch.setattr(rehearsal.psycopg, "connect", connect)
    assert len(rehearsal.registry_digest("private-url")) == 64
    connect.assert_called_once_with("private-url", connect_timeout=10, autocommit=True)
    calls = connection.execute.call_args_list
    assert calls[0].args == ("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY",)
    assert calls[1].args == ("SET LOCAL statement_timeout='10s'",)
    assert calls[-1].args == ("ROLLBACK",)
    assert len(calls) == len(rehearsal.REGISTRY_TABLES) + 3


def test_shared_partial_write_registers_primary_before_failed_outcome(tmp_path):
    primary = LocalArtifactStore(tmp_path / "primary").put_bytes(
        b"synthetic", logical_name="batch.json"
    )
    error = ArtifactWriteError(primary_reference=primary, cause_type="ClientError")
    registry = Mock()
    record_partial_artifact(registry, "synthetic-run", kind="forecast_batch", error=error)
    assert registry.method_calls[0][0] == "register_artifact"
    assert registry.method_calls[1][0] == "fail_run"
    registry.register_artifact.assert_called_once_with(
        "synthetic-run", kind="forecast_batch", reference=primary
    )
    registry.complete_run.assert_not_called()


def test_shared_partial_write_keeps_registration_failure_visible(tmp_path):
    reference = LocalArtifactStore(tmp_path / "primary").put_bytes(
        b"synthetic", logical_name="batch.json"
    )
    registry = Mock()
    registry.register_artifact.side_effect = ValueError("credential-bearing exception")
    record_partial_artifact(
        registry,
        "run",
        kind="forecast_batch",
        error=ArtifactWriteError(primary_reference=reference, cause_type="ClientError"),
    )
    message = registry.fail_run.call_args.kwargs["error_message"]
    assert "registration failed" in message
    assert "credential-bearing" not in message


def test_child_diagnostics_are_captured_and_not_printed(monkeypatch, capsys):
    monkeypatch.setattr(
        rehearsal.subprocess,
        "run",
        Mock(
            return_value=SimpleNamespace(
                returncode=1, stdout="secret-url", stderr="secret-password"
            )
        ),
    )
    with pytest.raises(ValueError, match="child_exit_unexpected"):
        rehearsal.run_json(["trusted-child"], {})
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("finding", ["missing_object", "hash_mismatch", "failed_run", "none"])
def test_independent_audit_requires_exact_retained_failed_run_finding(
    tmp_path, monkeypatch, finding
):
    findings = [] if finding == "none" else [{"code": finding}]
    report = {
        "status": "findings",
        "findings": findings,
        "objects_verified": 2,
        "artifacts_seen": 2,
    }
    monkeypatch.setattr(rehearsal, "run_json", lambda *args, **kwargs: report)
    if finding == "failed_run":
        rehearsal.audit(tmp_path, configuration(), "audit.json", local=True)
        assert json.loads((tmp_path / "audit.json").read_text())["findings"] == findings
    else:
        with pytest.raises(ValueError):
            rehearsal.audit(tmp_path, configuration(), "audit.json", local=True)
        assert not (tmp_path / "audit.json").exists()


def test_main_retains_only_fixed_failure_receipt(tmp_path, monkeypatch, capsys):
    root = tmp_path / "new"
    monkeypatch.setattr(rehearsal.sys, "argv", ["rehearsal", "--output-root", str(root)])
    monkeypatch.setattr(
        rehearsal, "exercise", Mock(side_effect=RuntimeError("secret-password-url"))
    )
    assert rehearsal.main() == 1
    assert "secret-password-url" not in (root / "summary.json").read_text()
    assert "secret-password-url" not in capsys.readouterr().out
    assert rehearsal.main() == 2


def test_main_refuses_existing_or_symlinked_output_before_exercise(tmp_path, monkeypatch):
    existing = tmp_path / "existing"
    existing.mkdir()
    link = tmp_path / "link"
    link.symlink_to(existing, target_is_directory=True)
    execute = Mock()
    monkeypatch.setattr(rehearsal, "exercise", execute)
    for root in (existing, link / "new"):
        monkeypatch.setattr(rehearsal.sys, "argv", ["rehearsal", "--output-root", str(root)])
        assert rehearsal.main() == 2
    execute.assert_not_called()


@pytest.mark.parametrize(
    "scenario",
    ["success", "wrong_denial", "writable_reader", "nonempty_bucket", "bad_primary_audit"],
)
def test_wired_exercise_orders_verification_before_repair_and_preserves_rows(
    tmp_path, monkeypatch, scenario
):
    from botocore.exceptions import ClientError

    from edgar_moe.forward.artifacts import R2ArtifactStore
    from edgar_moe.forward.database import RegistryDatabase
    from edgar_moe.forward.reconciliation import reconcile_registry_artifacts
    from edgar_moe.forward.registry import ForwardRegistry
    from scripts.seed_forward_restore_fixture import seed_fixture

    root = tmp_path / "reports"
    root.mkdir()
    url = f"sqlite:///{tmp_path / 'fixture.sqlite3'}"
    objects = {}
    events = []
    if scenario == "nonempty_bucket":
        objects["unexpected-existing"] = (b"existing", "a" * 64)

    class Client:
        exceptions = SimpleNamespace(ClientError=ClientError)

        def __init__(self, reader):
            self.reader = reader

        def list_objects_v2(self, **kwargs):
            assert kwargs["Bucket"] == rehearsal.BUCKET
            return {"Contents": [{"Key": key} for key in objects]}

        def head_object(self, *, Bucket, Key):
            assert Bucket == rehearsal.BUCKET
            if Key not in objects:
                raise ClientError({"ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")
            return {"Metadata": {"sha256": objects[Key][1]}}

        def put_object(self, *, Bucket, Key, Body, Metadata):
            assert Bucket == rehearsal.BUCKET
            if self.reader and scenario != "writable_reader":
                code = 500 if scenario == "wrong_denial" else 403
                raise ClientError(
                    {
                        "ResponseMetadata": {"HTTPStatusCode": code},
                        "Error": {"Code": "AccessDenied"},
                    },
                    "PutObject",
                )
            objects[Key] = (Body, Metadata["sha256"])

        def upload_file(self, source, bucket, key, *, ExtraArgs):
            self.put_object(
                Bucket=bucket,
                Key=key,
                Body=Path(source).read_bytes(),
                Metadata=ExtraArgs["Metadata"],
            )

    def remote(reader):
        value = object.__new__(R2ArtifactStore)
        value.bucket = rehearsal.BUCKET
        value.client = Client(reader)
        return value

    writer = remote(False)
    monkeypatch.setattr(rehearsal, "observe", lambda *args, **kwargs: 18)
    monkeypatch.setattr(
        rehearsal, "audit_auditor_role", lambda *args, **kwargs: {"status": "passed"}
    )
    monkeypatch.setattr(rehearsal, "store", lambda env, *, reader: remote(reader))
    monkeypatch.setattr(
        rehearsal.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(rehearsal, "seed_fixture", lambda _url, path: seed_fixture(url, path))
    monkeypatch.setattr(rehearsal, "RegistryDatabase", lambda _url, **kwargs: RegistryDatabase(url))

    def digest(_url):
        database = RegistryDatabase(url)
        registry = ForwardRegistry(database)
        try:
            return hashlib.sha256(
                json.dumps(
                    [registry.list_artifacts(), registry.list_runs(), registry.list_forecasts()],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        finally:
            database.dispose()

    monkeypatch.setattr(rehearsal, "registry_digest", digest)

    def audit(_root, env, name, **kwargs):
        events.append(name)
        if scenario == "bad_primary_audit" and kwargs.get("local"):
            raise ValueError("primary_not_verified")

    monkeypatch.setattr(rehearsal, "audit", audit)

    def repair(command, env):
        assert command[-2:] == ["forward-reconcile-artifacts", "--repair"]
        assert (
            env["EDGAR_MOE_REGISTRY_DATABASE_URL"]
            == configuration()["REHEARSAL_AUDITOR_DATABASE_URL"]
        )
        assert "primary-independent-audit.json" in events and "missing-mirror-audit.json" in events
        events.append("repair")
        database = RegistryDatabase(url)
        try:
            return reconcile_registry_artifacts(
                ForwardRegistry(database),
                LocalArtifactStore(root / "synthetic-primary"),
                mirror=writer,
                repair=True,
            )
        finally:
            database.dispose()

    monkeypatch.setattr(rehearsal, "run_json", repair)
    if scenario == "success":
        rehearsal.exercise(root, configuration())
        assert events.count("repair") == 2 and len(objects) == 2
        assert json.loads((root / "preservation.json").read_bytes())["expected_failed_run_retained"]
    else:
        with pytest.raises(ValueError):
            rehearsal.exercise(root, configuration())
        assert "repair" not in events
