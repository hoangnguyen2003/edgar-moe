"""Minimal CPython reader against CI-only loopback TLS, never a Neon service.

The deliberately invented hostname exercises the hosted metadata branch while
libpq hostaddr pins every connection to 127.0.0.1. No provider DNS is queried.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import traceback
from importlib.metadata import distributions
from pathlib import Path


def run(bundle, config):
    if (
        sys.platform != "linux"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or sys.version_info[:2] != (3, 12)
        or config["scope"] != "isolated_linux_ci_loopback_tls"
        or config["hostaddr"] != "127.0.0.1"
        or config["port"] != 5432
        or any(name.startswith("PG") for name in os.environ)
    ):
        raise ValueError("isolated_linux_ci_required")
    manifest = json.loads((bundle / "source-manifest.json").read_bytes())
    for row in manifest["files"]:
        path = bundle / row["path"]
        raw = path.read_bytes()
        if (
            path.resolve() != path
            or not path.is_relative_to(bundle)
            or hashlib.sha256(raw).hexdigest() != row["sha256"]
        ):
            raise ValueError("verified_bundle_required")
    sys.path.insert(0, str(bundle))
    absent = ["numpy", "pandas", "torch", "sklearn", "duckdb", "polars", "fastapi", "boto3"]
    if any(importlib.util.find_spec(name) is not None for name in absent):
        raise ValueError("minimal_runtime_required")
    os.environ["PATH"] = "/nonexistent-ci-reader-path"
    subprocess.Popen = lambda *_a, **_k: (_ for _ in ()).throw(ValueError("subprocess_refused"))
    import snapshot_kernel as kernel
    from psycopg import connect
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    fields = dict(config["target"])
    fields["fixture"] = tuple(tuple(row) for row in fields["fixture"])
    target = kernel.Target(**fields)
    if target.host != "ep-invented-ci-fixture.c-4.ap-southeast-1.aws.neon.tech":
        raise ValueError("invented_ci_hostname_required")
    contract = kernel.SqlContract(**json.loads((bundle / "contract.json").read_bytes()))
    report = {
        "scope": config["scope"],
        "provider_requests": 0,
        "hosted_days": 0,
        "platform": sys.platform,
        "python": list(sys.version_info[:3]),
        "research_packages_absent": absent,
        "installed_distributions": sorted(
            [{"name": d.metadata["Name"], "version": d.version} for d in distributions()],
            key=lambda r: r["name"],
        ),
        "reads": [],
        "refusals": [],
        "reader_connections": 0,
    }
    base = {
        "host": target.host,
        "hostaddr": "127.0.0.1",
        "port": 5432,
        "dbname": target.database,
        "user": target.role,
        "password": config["reader_password"],
        "sslrootcert": config["ca_file"],
        "sslmode": "verify-full",
        "channel_binding": "require",
        "connect_timeout": 3,
        "application_name": "study_ci_tls_reader",
        "passfile": "/dev/null",
        "options": "-c search_path=public,pg_catalog -c statement_timeout=3000 -c lock_timeout=500 -c transaction_timeout=10000 -c idle_in_transaction_session_timeout=5000",
    }

    def engine_for(changes):
        def creator():
            report["reader_connections"] += 1
            return connect(**(base | changes))

        return create_engine("postgresql+psycopg://", creator=creator, poolclass=NullPool)

    for route in ["status", "forecasts", "ticker_filter", "performance"]:
        engine = engine_for({})
        try:
            with engine.connect() as connection:
                info = connection.connection.driver_connection.info
                assert info.pgconn.ssl_in_use == 1
                assert info.get_parameters()["channel_binding"] == "require"
                value = kernel.read_snapshot(connection, target, contract, route)
                raw = value.pop("body")
                assert hashlib.sha256(raw).hexdigest() == value["body_sha256"]
                assert (
                    value["backend_absence_verified"] is value["safe_for_public_release"] is False
                )
                report["reads"].append(
                    {
                        "route": route,
                        "verified_full_tls": True,
                        "required_channel_binding": True,
                        "same_snapshot": value["same_snapshot"],
                        "body_bytes": len(raw),
                        "total_us": value["total_us"],
                    }
                )
        finally:
            engine.dispose()
    for name, changes in [
        ("wrong_hostname", {"host": "ep-wrong-ci-fixture.c-4.ap-southeast-1.aws.neon.tech"}),
        ("untrusted_ca", {"sslrootcert": config["other_ca_file"]}),
    ]:
        engine = engine_for(changes)
        try:
            try:
                with engine.connect():
                    raise AssertionError("invalid_tls_was_accepted")
            except Exception as error:
                if isinstance(error, AssertionError):
                    raise
                report["refusals"].append({"case": name, "connection_refused": True})
        finally:
            engine.dispose()
    for name, changes in [
        ("unverified_sslmode", {"sslmode": "require"}),
        ("channel_binding_disabled", {"channel_binding": "disable"}),
    ]:
        engine = engine_for(changes)
        try:
            with engine.connect() as connection:
                # Count only attempted kernel statements; dialect initialization
                # precedes this wrapper and is a separate operation class.
                class BoundConnection:
                    calls = 0

                    def __getattr__(self, name):
                        return getattr(connection, name)

                    def exec_driver_sql(self, *args, **kwargs):
                        self.calls += 1
                        return connection.exec_driver_sql(*args, **kwargs)

                wrapper = BoundConnection()
                try:
                    kernel.read_snapshot(wrapper, target, contract, "status")
                except kernel.ReadRefused as error:
                    assert str(error) == "connection_binding_refused" and wrapper.calls == 0
                    report["refusals"].append({"case": name, "kernel_sql_attempts": 0})
                else:
                    raise AssertionError("invalid_binding_was_accepted")
        finally:
            engine.dispose()
    for name, module in list(sys.modules.items()):
        if name == "snapshot_kernel" or name == "edgar_moe" or name.startswith("edgar_moe."):
            source = getattr(module, "__file__", None)
            if source is not None and not Path(source).resolve().is_relative_to(bundle):
                raise ValueError("application_import_escaped_bundle")
    report.update(
        status="passed",
        all_application_imports_from_bundle=True,
        node_available=False,
        actual_neon_tls_verified=False,
        representative_volume_verified=False,
    )
    return report


if __name__ == "__main__":
    bundle = Path(sys.argv[1])
    config = json.load(sys.stdin)
    try:
        result = run(bundle, config)
    except Exception as error:
        result = {
            "scope": "isolated_linux_ci_loopback_tls",
            "status": "failed",
            "error_class": type(error).__name__,
        }
        allowed = {
            "target_refused",
            "connection_binding_refused",
            "sql_binding_refused",
            "begin_refused",
            "session_refused",
            "snapshot_refused",
            "schema_refused",
            "fixture_refused",
            "service_refused",
            "snapshot_postcheck_refused",
            "snapshot_changed",
            "snapshot_deadline",
            "rollback_refused",
            "rollback_unconfirmed",
            "response_refused",
            "response_size_refused",
        }
        if type(error).__name__ == "ReadRefused" and str(error) in allowed:
            result["fixed_refusal_code"] = str(error)
        result["source_frames"] = [
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(error.__traceback__)
            if Path(frame.filename).name in {"probe.py", "snapshot_kernel.py"}
        ]
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["status"] == "passed" else 1)
