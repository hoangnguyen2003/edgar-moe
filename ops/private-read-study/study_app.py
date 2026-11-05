"""Separate synthetic #290 study. No writer, dotenv, schema setup or public app.

Deployment/window/fixture configuration is explicit and independently staged.
The old pilot, its allowance and its expiry are not inputs to this service.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import BoundedSemaphore
from time import perf_counter_ns
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

ROUTES = {
    "/v1/status": "status",
    "/v1/forecasts": "forecasts",
    "/v1/ticker-filter": "ticker_filter",
    "/v1/performance": "performance",
}
SCOPE = "portfolio-hosted-read-study290-v1"
WINDOW = timedelta(days=7)


class StudyRefusal(ValueError):
    """Fixed reason codes; never include URLs, rows or driver error text."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True, repr=False)
class StudyConfig:
    policy: dict[str, Any]
    database_url: str = field(repr=False)
    bearer: str = field(repr=False)
    policy_sha256: str
    starts_at: datetime
    expires_at: datetime

    @classmethod
    def load(cls, policy: dict[str, Any], env: Mapping[str, str]) -> StudyConfig:
        try:
            digest = hashlib.sha256(canonical(policy)).hexdigest()
            if not hmac.compare_digest(digest, env.get("STUDY_POLICY_SHA256", "")):
                raise ValueError()
            start = datetime.fromisoformat(policy["starts_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(policy["expires_at"].replace("Z", "+00:00"))
            target = policy["target"]
            url = env.get("STUDY_READ_DATABASE_URL", "")
            parsed = urlsplit(url)
            # Parse exactly the libpq connection that will be opened, including
            # rejecting startup/hostaddr options rather than trusting URL labels.
            from psycopg.conninfo import conninfo_to_dict

            fields = conninfo_to_dict(url)
            allowed = {"host", "port", "dbname", "user", "password", "sslmode", "channel_binding"}
            if (
                set(policy)
                != {
                    "scope",
                    "study_id",
                    "starts_at",
                    "expires_at",
                    "target",
                    "contract_sha256",
                    "traffic",
                }
                or policy["scope"] != SCOPE
                or not re.fullmatch(r"portfolio290_[a-z0-9_]{8,40}", policy["study_id"])
                or start.utcoffset() != timedelta(0)
                or end.utcoffset() != timedelta(0)
                or end - start != WINDOW
                or policy["traffic"]
                != {
                    "hourly_pairs": 168,
                    "requests_per_pair": 2,
                    "verification_reads": 24,
                    "total_read_attempts": 360,
                }
                or target["kind"] != "neon-hosted"
                or any(name.startswith("PG") for name in env)
                or not re.fullmatch(r"study290_[a-z0-9_]{8,40}", target["database"])
                or not re.fullmatch(r"study290_reader_[a-z0-9_]{8,32}", target["role"])
                or parsed.scheme not in {"postgres", "postgresql"}
                or not parsed.password
                or set(fields) - allowed
                or fields.get("host") != target["host"]
                or int(fields.get("port", "5432")) != target["port"]
                or fields.get("dbname") != target["database"]
                or fields.get("user") != target["role"]
                or fields.get("sslmode") != "verify-full"
                or fields.get("channel_binding") != "require"
                or "-pooler" in target["host"]
                or not re.fullmatch(
                    r"ep-[a-z0-9-]+\.c-[0-9]+\.ap-southeast-1\.aws\.neon\.tech", target["host"]
                )
                or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", env.get("STUDY_BEARER_TOKEN", ""))
            ):
                raise ValueError()
            return cls(policy, url, env["STUDY_BEARER_TOKEN"], digest, start, end)
        except Exception:
            raise StudyRefusal("configuration_refused") from None


@dataclass(frozen=True)
class ReadResult:
    body: bytes
    timing_us: dict[str, int]
    same_snapshot: bool
    backend_absence_verified: bool


def read(config: StudyConfig, route: str, bundle: Path) -> ReadResult:
    """Use the reviewed same-snapshot kernel and two SELECT-only connections.

    The observer wakes an idle database, so its time remains in full HTTP and
    read-lifecycle timing. It is not labeled query-only or platform cold time.
    """
    import psycopg
    from snapshot_kernel import ReadRefused, SqlContract, Target, read_snapshot
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    started = perf_counter_ns()
    contract_raw = (bundle / "contract.json").read_bytes()
    if hashlib.sha256(contract_raw).hexdigest() != config.policy["contract_sha256"]:
        raise StudyRefusal("contract_refused")
    raw = json.loads(contract_raw)
    contract = SqlContract(
        **{
            name: raw[name]
            for name in ("session", "original_session", "schema", "fixture", "source_sha256")
        }
    )
    target_values = dict(config.policy["target"])
    target_values["fixture"] = tuple(tuple(row) for row in target_values["fixture"])
    target = Target(**target_values)
    # Serverless images need an explicit CA bundle: libpq's default otherwise
    # expects ~/.postgresql/root.crt, which this deployment does not provision.
    # The bundle is a hash-pinned runtime dependency, never an environment path.
    tls_options: dict[str, str] = {}
    if target.kind == "neon-hosted":
        import certifi

        tls_options["sslrootcert"] = certifi.where()
    observer = None
    engine = None
    result = None
    try:
        observer = psycopg.connect(
            config.database_url,
            autocommit=True,
            connect_timeout=5,
            application_name="study290_observer",
            **tls_options,
        )
        observer.execute("SET statement_timeout='3s'")
        observer.execute("SET lock_timeout='500ms'")
        observer_connected = perf_counter_ns()

        def other_sessions() -> int:
            row = observer.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE usename=current_user "
                "AND datname=current_database() AND pid<>pg_backend_pid()"
            ).fetchone()
            if row is None:
                raise StudyRefusal("observer_refused")
            return int(row[0])

        if other_sessions() != 0:
            raise StudyRefusal("preexisting_reader_sessions")
        engine = create_engine(
            "postgresql+psycopg://",
            poolclass=NullPool,
            creator=lambda: psycopg.connect(
                config.database_url,
                connect_timeout=5,
                application_name="study290_reader",
                **tls_options,
            ),
        )
        with engine.connect() as connection:
            reader_connected = perf_counter_ns()
            if other_sessions() != 1:
                raise StudyRefusal("reader_presence_unconfirmed")
            result = read_snapshot(connection, target, contract, route)
        engine.dispose()
        engine = None
        if other_sessions() != 0:
            raise StudyRefusal("reader_absence_unconfirmed")
        observer.close()
        if not observer.closed:
            raise StudyRefusal("observer_close_unconfirmed")
        return ReadResult(
            result["body"],
            {
                "observer_connect": (observer_connected - started) // 1000,
                "reader_connect": (reader_connected - observer_connected) // 1000,
                "integrity": result["integrity_us"],
                "service": result["service_us"],
                "snapshot": result["total_us"],
                "lifecycle": (perf_counter_ns() - started) // 1000,
            },
            result["same_snapshot"],
            True,
        )
    except StudyRefusal:
        raise
    except ReadRefused as error:
        # Kernel reasons are fixed stage codes, never driver text. Keep them
        # useful to a private operator; the HTTP boundary still returns one
        # generic error body and stops this process.
        reason = str(error)
        raise StudyRefusal(
            reason if re.fullmatch(r"[a-z_]{1,64}", reason) else "snapshot_refused"
        ) from None
    except Exception:
        raise StudyRefusal("read_or_closure_refused") from None
    finally:
        if engine is not None:
            engine.dispose()
        if observer is not None:
            observer.close()


def create_app(
    policy: dict[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    reader: Callable[[StudyConfig, str, Path], ReadResult] = read,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    bundle: Path | None = None,
) -> FastAPI:
    bundle = bundle or Path(__file__).resolve().parent
    environment = dict(os.environ if env is None else env)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    gate = BoundedSemaphore(1)
    admitted = 0  # Process marker only; never advertised as a global quota.
    stopped = False
    try:
        declared = (
            policy if policy is not None else json.loads((bundle / "study-policy.json").read_text())
        )
        config = StudyConfig.load(declared, environment)
    except Exception:
        config = None

    @app.middleware("http")
    async def private_boundary(request: Request, call_next: Callable[..., Any]) -> Response:
        nonlocal admitted, stopped
        tokens = request.headers.getlist("authorization")
        expected = "Bearer " + environment.get("STUDY_BEARER_TOKEN", "")
        if len(tokens) != 1 or len(expected) < 50 or not hmac.compare_digest(tokens[0], expected):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
        elif config is None:
            response = JSONResponse({"error": "study_unavailable"}, status_code=503)
        elif clock() < config.starts_at:
            response = JSONResponse({"error": "study_not_started"}, status_code=425)
        elif clock() >= config.expires_at:
            response = JSONResponse({"error": "study_expired"}, status_code=410)
        elif stopped:
            response = JSONResponse({"error": "study_stopped"}, status_code=503)
        elif request.method != "GET":
            response = JSONResponse({"error": "read_only"}, status_code=405)
        elif request.url.path not in ROUTES:
            response = JSONResponse({"error": "not_found"}, status_code=404)
        elif request.url.query:
            response = JSONResponse({"error": "invalid_query"}, status_code=400)
        elif not gate.acquire(blocking=False):
            response = JSONResponse({"error": "process_busy"}, status_code=429)
        else:
            first = admitted == 0
            admitted += 1
            try:
                # A synchronous database lifecycle runs outside the ASGI event
                # loop. No background task outlives the authenticated request.
                from starlette.concurrency import run_in_threadpool

                result = await run_in_threadpool(reader, config, ROUTES[request.url.path], bundle)
                if not (
                    result.same_snapshot is True
                    and result.backend_absence_verified is True
                    and set(result.timing_us)
                    == {
                        "observer_connect",
                        "reader_connect",
                        "integrity",
                        "service",
                        "snapshot",
                        "lifecycle",
                    }
                    and all(
                        type(value) is int and 0 <= value <= 20_000_000
                        for value in result.timing_us.values()
                    )
                    and type(result.body) is bytes
                    and 0 < len(result.body) <= 262144
                ):
                    raise StudyRefusal("read_acknowledgement_refused")
                response = Response(result.body, media_type="application/json")
                response.headers["X-Study-Read-Timing"] = ";".join(
                    [f"{name}_us={value}" for name, value in sorted(result.timing_us.items())]
                    + ["process=" + ("first" if first else "subsequent")]
                )
                response.headers["X-Study-Policy-SHA256"] = config.policy_sha256
            except Exception:
                stopped = True
                response = JSONResponse({"error": "read_unavailable"}, status_code=503)
            finally:
                gate.release()
        for name in ("Cache-Control", "CDN-Cache-Control", "Vercel-CDN-Cache-Control"):
            response.headers[name] = "private, no-store"
        response.headers["Vary"] = "Authorization"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    return app
