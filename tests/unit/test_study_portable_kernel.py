"""Portable bindings must refuse before SQL; builders cannot silently widen source/output scope."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

PREP = (
    Path(__file__).resolve().parents[2]
    / "ops/private-read-admission/study-prep-20261027/portable-kernel-v1"
)
sys.path.insert(0, str(PREP))
import snapshot_kernel as kernel  # noqa: E402

del sys.path[0]
SPEC = importlib.util.spec_from_file_location("study_kernel_builder", PREP / "build.py")
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def target():
    return kernel.Target(
        scope="separate-study290-snapshot-v1",
        kind="neon-hosted",
        host="ep-invented-unit-fixture.c-4.ap-southeast-1.aws.neon.tech",
        port=5432,
        database="study290_invented_fixture",
        role="study290_reader_invented_fixture",
        profile_sha256="a" * 64,
        schema_sha256="b" * 64,
        fixture=tuple((name, 1, "c" * 64) for name in kernel.TABLES),
        sql_contract_sha256="d" * 64,
        sample_now="2026-10-27T00:00:00+00:00",
    )


def connection(value):
    info = SimpleNamespace(
        host=value.host,
        port=value.port,
        dbname=value.database,
        user=value.role,
        pgconn=SimpleNamespace(ssl_in_use=1),
        get_parameters=lambda: {"sslmode": "verify-full", "channel_binding": "require"},
    )
    return SimpleNamespace(
        connection=SimpleNamespace(driver_connection=SimpleNamespace(info=info)),
        in_transaction=lambda: False,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", "old-p0-scope"),
        ("host", "127.0.0.1"),
        ("host", "postgresql://owner:secret@host/db"),
        ("port", True),
        ("port", 6543),
        ("database", "private_read_pilot_20261012"),
        ("role", "private_read_pilot_reader_20261012"),
        ("kind", "environment"),
        ("profile_sha256", "unknown"),
        ("fixture", ()),
        ("sample_now", "2026-10-27T00:00:00"),
        ("sample_now", "2026-10-27T00:00:00+07:00"),
    ],
)
def test_unbound_or_old_pilot_target_cannot_be_reused(field, value):
    with pytest.raises(kernel.ReadRefused, match="^target_refused$"):
        replace(target(), **{field: value})


@pytest.mark.parametrize(
    "sslmode,binding,tls",
    [
        ("require", "require", 1),
        ("prefer", "require", 1),
        ("verify-full", "prefer", 1),
        ("verify-full", None, 1),
        ("verify-full", "require", 0),
    ],
)
def test_hosted_binding_requires_verified_tls_and_channel_binding_before_sql(sslmode, binding, tls):
    value = target()
    conn = connection(value)
    info = conn.connection.driver_connection.info
    info.pgconn.ssl_in_use = tls
    info.get_parameters = lambda: {"sslmode": sslmode, "channel_binding": binding}
    with pytest.raises(kernel.ReadRefused, match="^connection_binding_refused$"):
        value.check_connection(conn)


@pytest.mark.parametrize("field", ["host", "port", "dbname", "user"])
def test_different_driver_target_refuses_without_querying(field):
    value = target()
    conn = connection(value)
    setattr(conn.connection.driver_connection.info, field, "foreign")
    with pytest.raises(kernel.ReadRefused, match="^connection_binding_refused$"):
        value.check_connection(conn)


def test_expected_driver_metadata_can_be_checked_without_opening_or_querying():
    value = target()
    assert value.check_connection(connection(value)) is None
    conn = connection(value)
    conn.in_transaction = lambda: True
    with pytest.raises(kernel.ReadRefused, match="connection_binding_refused"):
        value.check_connection(conn)


@pytest.mark.parametrize("route", [None, True, "sql", "forecasts?offset=1", "performance;DELETE"])
def test_unknown_or_caller_parameterized_route_refuses_before_connection_access(route):
    with pytest.raises(kernel.ReadRefused, match="^fixed_route_required$"):
        kernel.read_snapshot(object(), target(), object(), route)


def test_wrong_sql_contract_refuses_before_connection_access():
    with pytest.raises(kernel.ReadRefused, match="^sql_binding_refused$"):
        kernel.read_snapshot(object(), target(), SimpleNamespace(sha256="0" * 64), "status")


@pytest.mark.parametrize("location", ["outside", "existing", "alias"])
def test_invalid_bundle_destination_fails_before_git_or_node(tmp_path, monkeypatch, location):
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "application_sources", lambda: pytest.fail("source access"))
    root = tmp_path / "data/artifacts"
    root.mkdir(parents=True)
    if location == "outside":
        output = tmp_path / "foreign"
    elif location == "existing":
        output = root / "existing"
        output.mkdir()
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        output = alias / "new"
    with pytest.raises(ValueError, match="new_private_bundle_directory_required"):
        builder.build(output)


@pytest.mark.parametrize("path", tuple(builder.SQL_FILES))
def test_changed_sql_source_cannot_be_executed_by_builder(monkeypatch, path):
    monkeypatch.setattr(builder, "SQL_FILES", {path: "0" * 64})
    monkeypatch.setattr(builder.subprocess, "run", lambda *_a, **_k: pytest.fail("Node execution"))
    with pytest.raises(ValueError, match="fixed_sql_source_changed"):
        builder.sql_contract()


def test_begin_failure_is_redacted_at_the_kernel_boundary():
    value = target()
    conn = connection(value)

    def failing_begin():
        raise RuntimeError("postgresql://invented:private-value@invented/db")

    conn.begin = failing_begin
    with pytest.raises(kernel.ReadRefused, match="^begin_refused$") as error:
        kernel.read_snapshot(
            conn, value, SimpleNamespace(sha256=value.sql_contract_sha256), "status"
        )
    assert error.value.__suppress_context__ is True


def test_namespace_parent_has_no_fabricated_source_file(monkeypatch):
    sources = {
        "src/edgar_moe/__init__.py": b"",
        "src/edgar_moe/forward/__init__.py": b"",
        "src/edgar_moe/forward/registry.py": b"from edgar_moe.utils.hashing import hash_file\n",
        "src/edgar_moe/utils/hashing.py": b"def hash_file(): pass\n",
        "src/edgar_moe/api/__init__.py": b"",
        "src/edgar_moe/api/models.py": b"",
    }
    monkeypatch.setattr(builder, "git_source", sources.get)
    assert builder.application_sources() == sources


def test_changed_dependency_lock_refuses_before_source_or_node_access(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "DEPENDENCY_LOCK_SHA256", "0" * 64)
    monkeypatch.setattr(builder, "application_sources", lambda: pytest.fail("source access"))
    output = tmp_path / "data/artifacts/new-bundle"
    with pytest.raises(ValueError, match="fixed_dependency_lock_changed"):
        builder.build(output)
    assert not output.exists()
