"""One explicitly authorized synthetic rehearsal on a fresh Postgres/R2 target.

Never resets a target, deletes objects, trains, forecasts or reads provider data.
Raw child-process/SDK diagnostics stay in memory, never logs or retained reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

from edgar_moe.forward.artifacts import (
    ArtifactWriteError,
    LocalArtifactStore,
    MirroredArtifactStore,
    R2ArtifactStore,
)
from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.domain import RunRegistration
from edgar_moe.forward.partial_write import record_partial_artifact
from edgar_moe.forward.registry import ForwardRegistry
from edgar_moe.forward.restore_rehearsal import REGISTRY_TABLES
from scripts.seed_forward_restore_fixture import seed_fixture
from scripts.verify_postgres_auditor import audit_auditor_role
from scripts.verify_restore_identities import endpoint, observe

DATABASE = "edgar_recovery_rehearsal_20261004"
BUCKET = "edgar-moe-recovery-rehearsal-20261004"
CONFIRM = "I_UNDERSTAND_ISOLATED_FAILURE"
RUN_ID = "isolated-recovery-failed-run"
REQUIRED = (
    "SOURCE_DATABASE_URL",
    "PROTECTED_R2_BUCKET",
    "REHEARSAL_DATABASE_URL",
    "REHEARSAL_AUDITOR_DATABASE_URL",
    "REHEARSAL_R2_ENDPOINT_URL",
    "REHEARSAL_R2_BUCKET",
    "REHEARSAL_R2_ACCESS_KEY_ID",
    "REHEARSAL_R2_SECRET_ACCESS_KEY",
    "REHEARSAL_R2_AUDITOR_ACCESS_KEY_ID",
    "REHEARSAL_R2_AUDITOR_SECRET_ACCESS_KEY",
)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def validate_scope(env: Mapping[str, str]) -> None:
    """Pure fail-closed checks before any provider connection or mutation."""
    require(env.get("CONFIRM_ISOLATED_FAILURE") == CONFIRM, "confirmation_required")
    require(all(env.get(k, "").strip() for k in REQUIRED), "missing_configuration")
    source = endpoint(env["SOURCE_DATABASE_URL"])
    owner = endpoint(env["REHEARSAL_DATABASE_URL"])
    reader = endpoint(env["REHEARSAL_AUDITOR_DATABASE_URL"])
    require(source[0] != owner[0], "source_endpoint_not_distinct")
    require(owner[2] == DATABASE, "unapproved_target_database")
    require(owner[:3] == reader[:3] and owner[3] != reader[3], "reader_identity_invalid")
    require(env["REHEARSAL_R2_BUCKET"] == BUCKET, "unapproved_target_bucket")
    require(env["PROTECTED_R2_BUCKET"] != BUCKET, "production_bucket_conflict")
    parsed = urlsplit(env["REHEARSAL_R2_ENDPOINT_URL"])
    host = parsed.hostname or ""
    account = host.removesuffix(".r2.cloudflarestorage.com")
    require(
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and not parsed.port
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and host.endswith(".r2.cloudflarestorage.com")
        and len(account) == 32
        and all(c in "0123456789abcdef" for c in account),
        "invalid_r2_endpoint",
    )
    require(
        env["REHEARSAL_R2_ACCESS_KEY_ID"] != env["REHEARSAL_R2_AUDITOR_ACCESS_KEY_ID"]
        and env["REHEARSAL_R2_SECRET_ACCESS_KEY"] != env["REHEARSAL_R2_AUDITOR_SECRET_ACCESS_KEY"],
        "r2_writer_reader_identity_conflict",
    )


def child_environment() -> dict[str, str]:
    """No implicit runner/source/SDK credentials or project dotenv defaults."""
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("EDGAR_MOE_", "REHEARSAL_", "SOURCE_", "PROTECTED_", "AUDITOR_", "AWS_")
        )
    }


def write_report(root: Path, name: str, report: dict[str, Any]) -> None:
    with (root / name).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)


def run_json(command: list[str], env: dict[str, str], *, allowed_exit: int = 0) -> dict[str, Any]:
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
    require(result.returncode == allowed_exit, "child_exit_unexpected")
    payload = json.loads(result.stdout)
    require(isinstance(payload, dict), "invalid_child_report")
    return dict(payload)


def registry_digest(url: str) -> str:
    """Hash complete synthetic registry rows in memory, never retain row values."""
    rows: dict[str, Any] = {}
    with psycopg.connect(url, connect_timeout=10, autocommit=True) as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='10s'")
        for table in REGISTRY_TABLES:
            query = sql.SQL(
                "SELECT row_to_json(t) FROM public.{} t ORDER BY to_jsonb(t)::text"
            ).format(sql.Identifier(table))
            rows[table] = [row[0] for row in conn.execute(query).fetchall()]
        conn.execute("ROLLBACK")
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def store(env: Mapping[str, str], *, reader: bool) -> R2ArtifactStore:
    label = "REHEARSAL_R2_AUDITOR" if reader else "REHEARSAL_R2"
    remote = R2ArtifactStore(
        endpoint_url=env["REHEARSAL_R2_ENDPOINT_URL"],
        bucket=BUCKET,
        access_key_id=env[label + "_ACCESS_KEY_ID"],
        secret_access_key=env[label + "_SECRET_ACCESS_KEY"],
    )
    # Bound SDK retries/timeouts, including the deliberate authorization denial.
    import boto3
    from botocore.config import Config

    remote.client = boto3.client(
        "s3",
        endpoint_url=env["REHEARSAL_R2_ENDPOINT_URL"],
        region_name="auto",
        aws_access_key_id=env[label + "_ACCESS_KEY_ID"],
        aws_secret_access_key=env[label + "_SECRET_ACCESS_KEY"],
        config=Config(connect_timeout=10, read_timeout=10, retries={"max_attempts": 1}),
    )
    return remote


def object_count(remote: R2ArtifactStore) -> int:
    response = remote.client.list_objects_v2(Bucket=BUCKET, MaxKeys=10)
    require(not response.get("IsTruncated", False), "unexpected_bucket_volume")
    return len(response.get("Contents", []))


def audit(
    root: Path, env: Mapping[str, str], name: str, *, local: bool, missing: int = 0, failed: int = 1
) -> None:
    child = child_environment()
    child.update(AUDITOR_DATABASE_URL=env["REHEARSAL_AUDITOR_DATABASE_URL"])
    command = [
        str(Path("tools/evidence-auditor/rehearsal-auditor").resolve()),
        "-timeout",
        "90s",
        "-max-object-bytes",
        "8192",
    ]
    if local:
        command += ["-local-root", str((root / "synthetic-primary").resolve())]
    else:
        child.update(
            AUDITOR_R2_ENDPOINT_URL=env["REHEARSAL_R2_ENDPOINT_URL"],
            AUDITOR_R2_BUCKET=BUCKET,
            AUDITOR_R2_ACCESS_KEY_ID=env["REHEARSAL_R2_AUDITOR_ACCESS_KEY_ID"],
            AUDITOR_R2_SECRET_ACCESS_KEY=env["REHEARSAL_R2_AUDITOR_SECRET_ACCESS_KEY"],
        )
    report = run_json(command, child, allowed_exit=1 if failed or missing else 0)
    expected = Counter({"failed_run": failed, "missing_object": missing})
    observed = Counter(item["code"] for item in report["findings"])
    require(+observed == +expected, "independent_audit_unexpected_findings")
    require(
        report["status"] == ("findings" if failed or missing else "passed"), "audit_status_mismatch"
    )
    require(
        report["objects_verified"] == report["artifacts_seen"] - missing,
        "audit_coverage_incomplete",
    )
    write_report(root, name, report)


def exercise(root: Path, env: Mapping[str, str]) -> None:
    validate_scope(env)
    owner_url, reader_url = env["REHEARSAL_DATABASE_URL"], env["REHEARSAL_AUDITOR_DATABASE_URL"]
    observe(owner_url, endpoint(owner_url), empty=True)
    observe(reader_url, endpoint(reader_url), empty=True)
    permission = audit_auditor_role(reader_url, profile="empty-restore-target")
    require(permission["status"] == "passed", "empty_reader_permissions_failed")
    write_report(root, "empty-reader-permissions.json", permission)
    writer, reader = store(env, reader=False), store(env, reader=True)
    require(object_count(writer) == 0 and object_count(reader) == 0, "test_bucket_not_empty")
    write_report(
        root,
        "preflight.json",
        {
            "status": "passed",
            "target_empty": True,
            "bucket_empty": True,
            "source_endpoint_distinct": True,
        },
    )
    # Last check before initializing a fresh target; never clear or reuse it.
    observe(owner_url, endpoint(owner_url), empty=True)
    migration_env = child_environment()
    migration_env["EDGAR_MOE_REGISTRY_DATABASE_URL"] = owner_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=migration_env,
        capture_output=True,
        timeout=60,
    )
    require(result.returncode == 0, "migration_failed")
    seed_fixture(owner_url, root / "synthetic-primary")
    permission = audit_auditor_role(reader_url, profile="registry-reader")
    require(permission["status"] == "passed", "restored_reader_permissions_failed")
    write_report(root, "reader-permissions.json", permission)
    database = RegistryDatabase(
        owner_url, pool_size=1, max_overflow=0, pool_timeout=5, statement_timeout_ms=10000
    )
    registry = ForwardRegistry(database, actor="isolated-recovery-rehearsal")
    primary = LocalArtifactStore(root / "synthetic-primary")
    try:
        baseline = registry.list_artifacts()
        require(len(baseline) == 1, "unexpected_fixture_artifacts")
        key = str(baseline[0]["uri"]).removeprefix("local://")
        reference = writer.put_file(primary.root / key, logical_name="forecast-batch.json")
        require(reference.sha256 == baseline[0]["sha256"], "baseline_mirror_mismatch")
        audit(root, env, "baseline-r2-audit.json", local=False, failed=0)
        registry.start_run(
            RunRegistration(
                run_type="forecast",
                as_of=datetime.now(UTC),
                code_revision="isolated-synthetic-recovery",
                config_hash="c" * 64,
                dataset_id="restore-fixture-dataset",
                model_id="restore-fixture-model",
                details={"fixture": "isolated-partial-write-rehearsal"},
            ),
            run_id=RUN_ID,
        )
        try:
            MirroredArtifactStore(primary, reader).put_bytes(
                b'{"fixture":"isolated-partial-write-rehearsal","schema_version":1}',
                logical_name="forecast-batch.json",
            )
        except ArtifactWriteError as error:
            cause = error.__cause__
            response = getattr(cause, "response", {})
            require(
                getattr(cause, "operation_name", None) == "PutObject"
                and response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 403
                and response.get("Error", {}).get("Code") == "AccessDenied",
                "expected_put_authorization_denial_not_observed",
            )
            record_partial_artifact(registry, RUN_ID, kind="forecast_batch", error=error)
        else:
            raise ValueError("read_only_token_unexpectedly_wrote")
        require(object_count(reader) == 1, "denied_write_changed_bucket")
        require(len(registry.list_artifacts()) == 2, "primary_reference_not_preserved")
        require(
            any(r["run_id"] == RUN_ID and r["status"] == "failed" for r in registry.list_runs()),
            "failed_run_not_preserved",
        )
        write_report(
            root,
            "failure.json",
            {
                "status": "passed",
                "put_access_denied": True,
                "primary_reference_preserved": True,
                "failed_run_preserved": True,
            },
        )
        audit(root, env, "primary-independent-audit.json", local=True)
        audit(root, env, "missing-mirror-audit.json", local=False, missing=1)
        before = registry_digest(reader_url)
        repair_env = child_environment()
        repair_env.update(
            EDGAR_MOE_REGISTRY_DATABASE_URL=reader_url,
            EDGAR_MOE_ARTIFACT_DIR=str(primary.root),
            EDGAR_MOE_ARTIFACT_BACKEND="local",
            EDGAR_MOE_ARTIFACT_MIRROR_BACKEND="r2",
            EDGAR_MOE_R2_ENDPOINT_URL=env["REHEARSAL_R2_ENDPOINT_URL"],
            EDGAR_MOE_R2_BUCKET=BUCKET,
            EDGAR_MOE_R2_ACCESS_KEY_ID=env["REHEARSAL_R2_ACCESS_KEY_ID"],
            EDGAR_MOE_R2_SECRET_ACCESS_KEY=env["REHEARSAL_R2_SECRET_ACCESS_KEY"],
        )
        for index in (1, 2):
            report = run_json(
                [sys.executable, "-m", "edgar_moe.cli", "forward-reconcile-artifacts", "--repair"],
                repair_env,
            )
            require(
                report["status"] == "passed" and report["mirrored_artifacts"] == 2,
                "repair_incomplete",
            )
            require(registry_digest(reader_url) == before, "repair_changed_registry")
            require(object_count(reader) == 2, "repair_object_count_mismatch")
            write_report(root, f"repair-{index}.json", report)
            audit(root, env, f"repaired-r2-audit-{index}.json", local=False)
        write_report(
            root,
            "preservation.json",
            {
                "status": "passed",
                "registry_rows_sha256": before,
                "registry_unchanged_by_both_repairs": True,
                "expected_failed_run_retained": True,
                "synthetic_fixture_only": True,
            },
        )
    finally:
        database.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    root = args.output_root.absolute()
    if root.exists() or any(p.is_symlink() for p in (root, *root.parents)):
        print('{"status":"failed","reason":"new_private_directory_required"}')
        return 2
    root.mkdir(parents=True, mode=0o700)
    try:
        exercise(root, os.environ)
    except Exception:
        write_report(
            root,
            "summary.json",
            {
                "status": "failed",
                "reason": "rehearsal_gate_failed",
                "raw_diagnostics_retained": False,
                "no_cleanup_or_retry_attempted": True,
            },
        )
        print('{"status":"failed","reason":"rehearsal_gate_failed"}')
        return 1
    write_report(
        root,
        "summary.json",
        {
            "schema_version": 1,
            "status": "passed",
            "observed_at": datetime.now(UTC).isoformat(),
            "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
            "commit": os.environ.get("GITHUB_SHA", "local"),
            "scope": "isolated_synthetic_partial_write_recovery",
            "raw_diagnostics_retained": False,
            "limitations": [
                "not_real_forecast_or_source_use_approval",
                "expected_failed_run_remains_failed",
                "not_provider_managed_backup_evidence",
            ],
        },
    )
    print('{"status":"passed","scope":"isolated_synthetic_partial_write_recovery"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
