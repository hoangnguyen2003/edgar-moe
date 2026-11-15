"""Short-lived, separately deployed synthetic pilot; never imported by the public API.

No dotenv, registry writer, schema creation, arbitrary SQL or artifact-store access.
Every authenticated read rechecks identity, effective grants and fixture bytes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import BoundedSemaphore
from time import perf_counter
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from psycopg import sql
from starlette.responses import JSONResponse, Response

from edgar_moe.forward.auditor_role import inspect_permissions
from edgar_moe.forward.endpoint import endpoint
from edgar_moe.forward.reader_role import READER_TABLES

DATABASE = "edgar_recovery_rehearsal_20261004"
ROLE = "edgar_recovery_auditor_20261004"
ROUTES = frozenset({"/v1/status", "/v1/forecasts", "/v1/performance"})
MAX_ROWS_PER_TABLE = 100
MAX_REQUESTS_PER_PROCESS = 60
MAX_LIFETIME = timedelta(hours=24)
HASH = re.compile(r"[0-9a-f]{64}\Z")
TOKEN = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")


class PilotRefusal(ValueError):
    """A fixed reason code, not driver text or caller input."""


@dataclass(frozen=True, repr=False)
class PilotConfig:
    database_url: str
    bearer_token: str
    endpoint_sha256: str
    registry_sha256: str
    expires_at: datetime

    @classmethod
    def load(cls, env: Mapping[str, str], now: datetime) -> PilotConfig:
        try:
            config = cls(
                database_url=env["PILOT_DATABASE_URL"],
                bearer_token=env["PILOT_BEARER_TOKEN"],
                endpoint_sha256=env["PILOT_ENDPOINT_SHA256"],
                registry_sha256=env["PILOT_REGISTRY_SHA256"],
                expires_at=datetime.fromisoformat(env["PILOT_EXPIRES_AT"].replace("Z", "+00:00")),
            )
            identity = endpoint(config.database_url)
            # URL options cannot override host, user, database or startup options.
            from psycopg.conninfo import conninfo_to_dict

            fields = conninfo_to_dict(config.database_url)
            if (
                identity[2:] != (DATABASE, ROLE)
                or identity[1] != 5432
                or not identity[0].endswith(".neon.tech")
                or fields.get("sslmode") != "verify-full"
                or fields.get("channel_binding") != "require"
                or not TOKEN.fullmatch(config.bearer_token)
                or not HASH.fullmatch(config.endpoint_sha256)
                or not HASH.fullmatch(config.registry_sha256)
                or endpoint_hash(identity) != config.endpoint_sha256
                or config.expires_at.tzinfo is None
                or not now < config.expires_at <= now + MAX_LIFETIME
            ):
                raise ValueError
            return config
        except Exception:
            raise PilotRefusal("configuration_invalid") from None


def endpoint_hash(identity: tuple[str, int, str, str]) -> str:
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def fixture_rows(connection: psycopg.Connection[Any], expected_hash: str) -> dict[str, Any]:
    """Bounded snapshot digest; raw synthetic rows never enter logs or error output."""
    rows: dict[str, Any] = {}
    for table in READER_TABLES:
        query = sql.SQL(
            "SELECT row_to_json(t) FROM public.{} t ORDER BY to_jsonb(t)::text LIMIT %s"
        ).format(sql.Identifier(table))
        values = [row[0] for row in connection.execute(query, (MAX_ROWS_PER_TABLE + 1,))]
        if len(values) > MAX_ROWS_PER_TABLE:
            raise PilotRefusal("fixture_volume_exceeded")
        rows[table] = values
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    if not hmac.compare_digest(digest, expected_hash):
        raise PilotRefusal("fixture_identity_changed")
    # The hash must be approved from the recovery receipt, never derived from
    # whichever database happens to be configured during deployment.
    datasets = rows["forward_datasets"]
    if (
        len(datasets) != 1
        or datasets[0].get("dataset_id") != "restore-fixture-dataset"
        or datasets[0].get("provenance") != {"fixture": "ci-restore-rehearsal"}
    ):
        raise PilotRefusal("synthetic_fixture_required")
    return rows


def response_payload(rows: dict[str, Any], path: str, query: Mapping[str, str]) -> dict[str, Any]:
    """Explicit projections omit evidence URIs, raw diagnostics and arbitrary JSON."""
    base: dict[str, Any] = {"data_mode": "synthetic_fixture", "research_only": True}
    if path == "/v1/status":
        return base | {
            "counts": {table: len(rows[table]) for table in READER_TABLES},
            "failed_runs": sum(row["status"] == "failed" for row in rows["forward_runs"]),
        }
    forecasts = rows["forward_forecasts"]
    if path == "/v1/performance":
        return base | {
            "forecast_count": len(forecasts),
            "matured_count": len(rows["forward_labels"]),
            "performance_claim": "not_evaluated_synthetic_pilot",
        }
    ticker = query.get("ticker")
    limit = int(query.get("limit", "1"))
    filtered = [row for row in forecasts if ticker is None or row["ticker"] == ticker]
    return base | {
        "total": len(filtered),
        "items": [
            {key: row[key] for key in ("ticker", "form", "entry_date", "score", "rank")}
            for row in sorted(filtered, key=lambda row: row["forecast_id"])[:limit]
        ],
    }


def read(config: PilotConfig, path: str, query: Mapping[str, str]) -> dict[str, Any]:
    with psycopg.connect(config.database_url, connect_timeout=3, autocommit=True) as conn:
        try:
            conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '3s'")
            conn.execute("SET LOCAL lock_timeout = '500ms'")
            conn.execute("SET LOCAL idle_in_transaction_session_timeout = '5s'")
            conn.execute("SET LOCAL search_path = pg_catalog")
            identity = conn.execute(
                "SELECT current_database(), current_user, session_user"
            ).fetchone()
            if identity != (DATABASE, ROLE, ROLE):
                raise PilotRefusal("observed_identity_mismatch")
            inspect_permissions(conn, profile="registry-reader")
            limits = conn.execute(
                "SELECT current_setting('max_connections')::int, "
                "current_setting('transaction_read_only'), current_setting('statement_timeout')"
            ).fetchone()
            if limits is None or limits[1:] != ("on", "3s"):
                raise PilotRefusal("session_policy_invalid")
            payload = response_payload(fixture_rows(conn, config.registry_sha256), path, query)
            payload["read_policy"] = {
                "transaction_read_only": True,
                "statement_timeout_ms": 3000,
                "connection_max_per_process": 1,
                "persistent_connections": 0,
                "server_max_connections": limits[0],
                "global_connection_cap_verified": False,
            }
            return payload
        finally:
            conn.execute("ROLLBACK")


def create_app(
    env: Mapping[str, str] | None = None,
    *,
    reader: Callable[[PilotConfig, str, Mapping[str, str]], dict[str, Any]] = read,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    """Fail closed even when deployment secrets are missing; imports never connect."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    try:
        config = PilotConfig.load(os.environ if env is None else env, clock())
    except PilotRefusal:
        config = None
    gate = BoundedSemaphore(1)
    requests_remaining = MAX_REQUESTS_PER_PROCESS
    first = True

    @app.middleware("http")
    async def boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response: Response
        if config is None:
            response = JSONResponse({"error": "pilot_unavailable"}, status_code=503)
        else:
            auth = request.headers.getlist("authorization")
            if (
                len(auth) != 1
                or len(auth[0]) > 140
                or not hmac.compare_digest(
                    auth[0].encode(), f"Bearer {config.bearer_token}".encode()
                )
            ):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                response.headers["WWW-Authenticate"] = "Bearer"
            elif clock() >= config.expires_at:
                response = JSONResponse({"error": "pilot_expired"}, status_code=410)
            elif request.method != "GET":
                response = JSONResponse({"error": "read_only"}, status_code=405)
                response.headers["Allow"] = "GET"
            else:
                response = await call_next(request)
        for name in ("Cache-Control", "CDN-Cache-Control", "Vercel-CDN-Cache-Control"):
            response.headers[name] = "private, no-store"
        response.headers["Vary"] = "Authorization"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    @app.get("/{path:path}")
    def get(request: Request, path: str) -> Response:
        nonlocal requests_remaining, first
        route = "/" + path
        if route not in ROUTES:
            return JSONResponse({"error": "not_found"}, status_code=404)
        pairs = list(request.query_params.multi_items())
        query = dict(pairs)
        allowed = {"ticker", "limit"} if route == "/v1/forecasts" else set()
        if (
            len(pairs) != len(query)
            or not set(query) <= allowed
            or ("ticker" in query and not re.fullmatch(r"[A-Z]{1,8}", query["ticker"]))
            or ("limit" in query and query["limit"] not in {"1", "2", "3", "4", "5"})
        ):
            return JSONResponse({"error": "invalid_query"}, status_code=400)
        if not gate.acquire(blocking=False):
            return JSONResponse({"error": "pilot_busy"}, status_code=429)
        try:
            if requests_remaining <= 0:
                return JSONResponse({"error": "pilot_budget_exhausted"}, status_code=429)
            requests_remaining -= 1
            started = perf_counter()
            state = "first" if first else "subsequent"
            first = False
            try:
                assert config is not None
                payload = reader(config, route, query)
                response = JSONResponse(payload)
            except Exception:
                # All unexpected driver, fixture and grant errors are withheld.
                response = JSONResponse({"error": "read_unavailable"}, status_code=503)
            response.headers["X-Pilot-Read-Timing"] = (
                f"read_ms={(perf_counter() - started) * 1000:.3f};process={state}"
            )
            return response
        finally:
            gate.release()

    return app
