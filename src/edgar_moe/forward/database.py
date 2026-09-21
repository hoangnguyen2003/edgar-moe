from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from edgar_moe.forward.immutability import append_only_statements
from edgar_moe.forward.models import IMMUTABLE_RECORD_TYPES, Base


class ImmutableRecordError(RuntimeError):
    """Raised when append-only registry evidence would be changed or deleted."""


@event.listens_for(Session, "before_flush")
def _protect_immutable_records(
    session: Session,
    _flush_context: Any,
    _instances: Any,
) -> None:
    for instance in session.deleted:
        if isinstance(instance, IMMUTABLE_RECORD_TYPES):
            raise ImmutableRecordError(
                f"{type(instance).__name__} is append-only and cannot be deleted"
            )
    for instance in session.dirty:
        if isinstance(instance, IMMUTABLE_RECORD_TYPES) and session.is_modified(
            instance, include_collections=True
        ):
            raise ImmutableRecordError(
                f"{type(instance).__name__} is append-only and cannot be updated"
            )


class RegistryDatabase:
    """SQLAlchemy database boundary shared by SQLite development and Postgres production."""

    _MAX_STATEMENT_TIMEOUT_MS = 600_000

    def __init__(
        self,
        database_url: str,
        *,
        echo: bool = False,
        pool_size: int | None = None,
        max_overflow: int | None = None,
        pool_timeout: float | None = None,
        read_only: bool = False,
        statement_timeout_ms: int | None = None,
    ) -> None:
        if not database_url.strip():
            raise ValueError("A forward registry database URL is required")
        _validate_pool_options(
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
        )
        _validate_session_options(
            read_only=read_only,
            statement_timeout_ms=statement_timeout_ms,
        )
        normalized = normalize_database_url(database_url)
        _prepare_sqlite_directory(normalized)
        connect_args = _build_connect_args(
            normalized,
            read_only=read_only,
            statement_timeout_ms=statement_timeout_ms,
        )
        self.url = normalized
        engine_options: dict[str, object] = {
            "echo": echo,
            "pool_pre_ping": True,
            "connect_args": connect_args,
        }
        # SQLite uses a dialect-specific pool that rejects QueuePool options.
        # Only the hosted Postgres reader receives the serverless bounds.
        if not normalized.startswith("sqlite"):
            if pool_size is not None:
                engine_options["pool_size"] = pool_size
            if max_overflow is not None:
                engine_options["max_overflow"] = max_overflow
            if pool_timeout is not None:
                engine_options["pool_timeout"] = pool_timeout
        self.engine = create_engine(normalized, **engine_options)
        if normalized.startswith("sqlite"):
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        self._sessions = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=Session,
        )

    def create_schema(self) -> None:
        """Create tables for local development and tests; production uses Alembic.

        The database-level append-only triggers are installed as well, so tests
        exercise the same write contract as a migrated registry.
        """
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            for statement in append_only_statements(connection.dialect.name):
                # text() escapes the % in PL/pgSQL RAISE formats for psycopg.
                connection.execute(text(statement))

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        database_session = self._sessions()
        try:
            yield database_session
            database_session.commit()
        except Exception:
            database_session.rollback()
            raise
        finally:
            database_session.close()

    def dispose(self) -> None:
        self.engine.dispose()


def normalize_database_url(database_url: str) -> str:
    value = database_url.strip()
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def _build_connect_args(
    normalized_url: str,
    *,
    read_only: bool,
    statement_timeout_ms: int | None,
) -> dict[str, object]:
    connect_args: dict[str, object] = {}
    if normalized_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    elif normalized_url.startswith("postgresql"):
        postgres_options: list[str] = []
        if read_only:
            postgres_options.append("-c default_transaction_read_only=on")
        if statement_timeout_ms is not None:
            postgres_options.append(f"-c statement_timeout={statement_timeout_ms}")
        if postgres_options:
            connect_args["options"] = " ".join(postgres_options)
    return connect_args


def _validate_pool_options(
    *,
    pool_size: int | None,
    max_overflow: int | None,
    pool_timeout: float | None,
) -> None:
    if pool_size is not None and pool_size < 1:
        raise ValueError("pool_size must be at least 1")
    if max_overflow is not None and max_overflow < 0:
        raise ValueError("max_overflow must be non-negative")
    if pool_timeout is not None and pool_timeout <= 0:
        raise ValueError("pool_timeout must be greater than 0")


def _validate_session_options(*, read_only: bool, statement_timeout_ms: int | None) -> None:
    if not isinstance(read_only, bool):
        raise ValueError("read_only must be a boolean")
    if statement_timeout_ms is not None and (
        isinstance(statement_timeout_ms, bool)
        or not isinstance(statement_timeout_ms, int)
        or not 1 <= statement_timeout_ms <= RegistryDatabase._MAX_STATEMENT_TIMEOUT_MS
    ):
        raise ValueError(
            "statement_timeout_ms must be between 1 and "
            f"{RegistryDatabase._MAX_STATEMENT_TIMEOUT_MS} milliseconds"
        )


def _prepare_sqlite_directory(database_url: str) -> None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
