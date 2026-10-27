"""Packagable same-snapshot reader; no connector, HTTP handler or allocation authority.

The trusted adapter supplies one fixed target/profile/SQL contract. A caller
supplies an already-open SELECT-only connection only after separate admission
and presence proof. This module cannot confer that permission or prove absence.
It has no Node subprocess, local harness import, environment lookup or pool.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter

from sqlalchemy.orm import Session

from edgar_moe.api.models import (
    ForwardForecastPage,
    ForwardPerformanceResponse,
    ForwardStatusResponse,
)
from edgar_moe.forward.registry import ForwardRegistry
from edgar_moe.forward.uncertainty import _bootstrap

RELATION_CAP_BYTES = 192 * 1024 * 1024
CONTROL_SQL = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
    "SET LOCAL statement_timeout='3s'",
    "SET LOCAL lock_timeout='500ms'",
    "SET LOCAL idle_in_transaction_session_timeout='5s'",
    "SET LOCAL transaction_timeout='10s'",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL DateStyle='ISO,YMD'",
    "SET LOCAL search_path=pg_catalog",
    "SET LOCAL extra_float_digits=3",
)
SNAPSHOT_SQL = """SELECT pg_backend_pid(), pg_current_snapshot()::text,
    current_setting('transaction_isolation'), current_setting('transaction_read_only'),
    current_setting('server_version_num')::int, current_setting('server_encoding')"""
TABLES = (
    "alembic_version",
    "forward_artifacts",
    "forward_audit_events",
    "forward_data_quality_checks",
    "forward_datasets",
    "forward_forecasts",
    "forward_labels",
    "forward_models",
    "forward_runs",
)
CAPS = {"status": 16384, "forecasts": 262144, "ticker_filter": 262144, "performance": 16384}


class ReadRefused(RuntimeError):
    """Fixed stage codes only; no driver messages, rejected rows or configuration."""


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def is_hash(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


@dataclass(frozen=True)
class SqlContract:
    session: str
    original_session: str
    schema: str
    fixture: str
    source_sha256: str

    def __post_init__(self):
        if not is_hash(self.source_sha256) or any(
            type(value) is not str or not 1 <= len(value.encode()) <= 40000
            for value in (self.session, self.original_session, self.schema, self.fixture)
        ):
            raise ReadRefused("sql_contract_refused")
        old = "<= 10485760)"
        if self.original_session.count(old) != 1 or self.session != self.original_session.replace(
            old, f"<= {RELATION_CAP_BYTES})"
        ):
            raise ReadRefused("sql_contract_refused")

    @property
    def sha256(self):
        return digest(
            {
                "session": self.session,
                "original_session": self.original_session,
                "schema": self.schema,
                "fixture": self.fixture,
                "source_sha256": self.source_sha256,
                "relation_cap_bytes": RELATION_CAP_BYTES,
            }
        )


@dataclass(frozen=True)
class Target:
    scope: str
    kind: str
    host: str
    port: int
    database: str
    role: str
    profile_sha256: str
    schema_sha256: str
    fixture: tuple[tuple[str, int, str], ...]
    sql_contract_sha256: str
    sample_now: str

    def __post_init__(self):
        try:
            now = datetime.fromisoformat(self.sample_now)
            valid = (
                self.scope == "separate-study290-snapshot-v1"
                and type(self.port) is int
                and all(
                    is_hash(value)
                    for value in (self.profile_sha256, self.schema_sha256, self.sql_contract_sha256)
                )
                and type(self.fixture) is tuple
                and len(self.fixture) == len(TABLES)
                and all(
                    type(row) is tuple
                    and len(row) == 3
                    and type(row[1]) is int
                    and 0 <= row[1] <= 180105
                    and is_hash(row[2])
                    for row in self.fixture
                )
                and tuple(row[0] for row in self.fixture) == TABLES
                and now.utcoffset() is not None
                and now.utcoffset().total_seconds() == 0
            )
            if self.kind == "local-proof":
                socket = Path(self.host)
                valid = (
                    valid
                    and bool(
                        re.fullmatch(
                            r"/private/tmp/edgar-study-local-20261027\.[a-z0-9_]{8}/socket",
                            self.host,
                        )
                    )
                    and socket.resolve() == socket
                    and socket.is_dir()
                    and socket.stat().st_uid == os.getuid()
                    and not socket.stat().st_mode & 0o077
                    and socket.parent.stat().st_uid == os.getuid()
                    and not socket.parent.stat().st_mode & 0o077
                    and (
                        self.port == 56487
                        and self.database == "edgar_study_workload_local_20261027"
                        and self.role == "edgar_study_local_reader_20261027"
                    )
                )
            elif self.kind == "neon-hosted":
                valid = (
                    valid
                    and bool(
                        re.fullmatch(
                            r"ep-[a-z0-9-]+\.c-[0-9]+\.ap-southeast-1\.aws\.neon\.tech", self.host
                        )
                    )
                    and self.port == 5432
                    and bool(re.fullmatch(r"study290_[a-z0-9_]{8,40}", self.database))
                    and bool(re.fullmatch(r"study290_reader_[a-z0-9_]{8,32}", self.role))
                )
            else:
                valid = False
            if not valid:
                raise ValueError()
        except (ValueError, TypeError, AttributeError, OSError):
            raise ReadRefused("target_refused") from None

    @property
    def sha256(self):
        return digest(self.__dict__)

    def check_connection(self, connection):
        try:
            info = connection.connection.driver_connection.info
            if (info.host, info.port, info.dbname, info.user) != (
                self.host,
                self.port,
                self.database,
                self.role,
            ) or connection.in_transaction():
                raise ValueError()
            if self.kind == "local-proof":
                if getattr(info, "hostaddr", "") not in (None, ""):
                    raise ValueError()
            elif (
                info.pgconn.ssl_in_use != 1
                or info.get_parameters().get("sslmode") != "verify-full"
                or info.get_parameters().get("channel_binding") != "require"
            ):
                raise ValueError()
        except Exception:
            raise ReadRefused("connection_binding_refused") from None


class SnapshotDatabase:
    """Use the externally owned transaction; closing an ORM session cannot end it."""

    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def session(self):
        session = Session(
            bind=self.connection,
            join_transaction_mode="rollback_only",
            autoflush=False,
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            session.close()


def read_snapshot(connection, target: Target, contract: SqlContract, route: str):
    """Fixed actual methods only, with all integrity work and closure in the same bound."""
    if type(route) is not str or route not in CAPS:
        raise ReadRefused("fixed_route_required")
    if contract.sha256 != target.sql_contract_sha256:
        raise ReadRefused("sql_binding_refused")
    target.check_connection(connection)
    started, stage, result = perf_counter(), "begin", None
    try:
        transaction = connection.begin()
    except Exception:
        raise ReadRefused("begin_refused") from None

    def in_window():
        if not 0 <= perf_counter() - started < 10:
            raise ReadRefused("snapshot_deadline")

    try:
        for command in CONTROL_SQL:
            in_window()
            connection.exec_driver_sql(command)
        stage = "session"
        identity = connection.exec_driver_sql(contract.session).mappings().one()
        if not (
            identity["database_name"] == target.database
            and identity["role_name"] == target.role
            and identity["session_role"] == target.role
            and identity["permissions_ok"] is True
            and identity["transaction_read_only"] == "on"
            and identity["statement_timeout"] == "3s"
        ):
            raise ReadRefused("session_refused")
        before = tuple(connection.exec_driver_sql(SNAPSHOT_SQL).one())
        if before[2:4] != ("repeatable read", "on") or not (
            180000 <= before[4] < 190000 and before[5] == "UTF8"
        ):
            raise ReadRefused("snapshot_refused")
        stage = "schema"
        if connection.exec_driver_sql(contract.schema).scalar_one() != target.schema_sha256:
            raise ReadRefused("schema_refused")
        stage = "fixture"
        rows = tuple(tuple(row) for row in connection.exec_driver_sql(contract.fixture).fetchall())
        if rows != target.fixture:
            raise ReadRefused("fixture_refused")
        in_window()
        integrity_us = int((perf_counter() - started) * 1000000)
        stage = "service"
        connection.exec_driver_sql("SET LOCAL search_path=pg_catalog,public")
        cache_before = _bootstrap.cache_info()._asdict()
        service = perf_counter()
        registry = ForwardRegistry(SnapshotDatabase(connection))
        if route == "status":
            body = registry.status(now=datetime.fromisoformat(target.sample_now))
            model = ForwardStatusResponse.model_validate(
                body | {"available": True, "message": "Separate generated study only"}
            )
        elif route in {"forecasts", "ticker_filter"}:
            body = registry.list_forecasts(
                limit=25, offset=0, ticker="SYNTH000" if route == "ticker_filter" else None
            )
            model = ForwardForecastPage.model_validate(body)
        else:
            body = registry.performance()
            model = ForwardPerformanceResponse.model_validate(body)
        service_us = int((perf_counter() - service) * 1000000)
        cache_after = _bootstrap.cache_info()._asdict()
        in_window()
        stage = "snapshot_postcheck"
        if not connection.in_transaction():
            raise ReadRefused("snapshot_ended_early")
        connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
        if tuple(connection.exec_driver_sql(SNAPSHOT_SQL).one()) != before:
            raise ReadRefused("snapshot_changed")
        stage = "response"
        raw = model.model_dump_json().encode()
        if not 0 < len(raw) <= CAPS[route]:
            raise ReadRefused("response_size_refused")
        result = {
            "body": raw,
            "body_bytes": len(raw),
            "body_sha256": hashlib.sha256(raw).hexdigest(),
            "target_sha256": target.sha256,
            "same_snapshot": True,
            "integrity_us": integrity_us,
            "service_us": service_us,
            "bootstrap_cache_before": cache_before,
            "bootstrap_cache_after": cache_after,
            "backend_absence_verified": False,
            "safe_for_public_release": False,
        }
    except ReadRefused:
        raise
    except Exception:
        raise ReadRefused(stage + "_refused") from None
    finally:
        try:
            transaction.rollback()
        except Exception:
            raise ReadRefused("rollback_refused") from None
    if connection.in_transaction():
        raise ReadRefused("rollback_unconfirmed")
    in_window()
    result["total_us"] = int((perf_counter() - started) * 1000000)
    return result
