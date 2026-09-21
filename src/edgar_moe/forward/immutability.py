"""Database-level enforcement of the append-only forward registry.

The ORM guard in :mod:`edgar_moe.forward.database` only protects writes made
through SQLAlchemy sessions. These triggers apply the same contract to every
client of the writer credential: evidence rows can never be updated, deleted,
or truncated, and a run may change only while it is ``running`` and only in its
outcome columns.

Alembic revision ``20260921_0002`` applies exactly these statements. Changing
them requires a new revision; ``test_append_only_statements_are_pinned`` fails
until the statements' golden hash is updated alongside that migration.
"""

from __future__ import annotations

EVIDENCE_TABLES: tuple[str, ...] = (
    "forward_datasets",
    "forward_models",
    "forward_forecasts",
    "forward_labels",
    "forward_artifacts",
    "forward_data_quality_checks",
    "forward_audit_events",
)
# Columns fixed when a run starts; only status, result_counts, error_message,
# and finished_at may change, once, when the run finishes.
_RUN_IDENTITY_COLUMNS: tuple[str, ...] = (
    "run_id",
    "run_type",
    "dataset_id",
    "model_id",
    "as_of",
    "code_revision",
    "config_hash",
    "parent_run_id",
    "details",
    "started_at",
)
_RUN_STATUSES = "('running', 'succeeded', 'failed')"

_POSTGRES_REJECT_FUNCTION = """CREATE OR REPLACE FUNCTION forward_reject_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'append-only table % rejects %', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$"""


def _postgres_run_function() -> str:
    # json has no equality operator in Postgres, so compare its text form.
    changed = "\n        OR ".join(
        f"NEW.{column}::text IS DISTINCT FROM OLD.{column}::text"
        if column == "details"
        else f"NEW.{column} IS DISTINCT FROM OLD.{column}"
        for column in _RUN_IDENTITY_COLUMNS
    )
    return f"""CREATE OR REPLACE FUNCTION forward_protect_run()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'append-only table forward_runs rejects DELETE'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.status <> 'running'
        OR NEW.status NOT IN {_RUN_STATUSES}
        OR {changed}
    THEN
        RAISE EXCEPTION 'forward run % may only record its outcome once', OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$"""


def append_only_statements(dialect: str) -> list[str]:
    """Return the idempotent DDL that enforces the registry contract."""
    if dialect == "postgresql":
        statements = [_POSTGRES_REJECT_FUNCTION, _postgres_run_function()]
        for table in EVIDENCE_TABLES:
            statements.append(
                f"CREATE OR REPLACE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE "
                f"ON {table} FOR EACH ROW EXECUTE FUNCTION forward_reject_mutation()"
            )
        statements.append(
            "CREATE OR REPLACE TRIGGER forward_runs_append_only BEFORE UPDATE OR DELETE "
            "ON forward_runs FOR EACH ROW EXECUTE FUNCTION forward_protect_run()"
        )
        for table in (*EVIDENCE_TABLES, "forward_runs"):
            statements.append(
                f"CREATE OR REPLACE TRIGGER {table}_no_truncate BEFORE TRUNCATE "
                f"ON {table} FOR EACH STATEMENT EXECUTE FUNCTION forward_reject_mutation()"
            )
        return statements
    if dialect == "sqlite":
        statements = []
        for table in EVIDENCE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                statements.append(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} "
                    f"BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, "
                    f"'append-only table {table} rejects {operation}'); END"
                )
        statements.append(
            "CREATE TRIGGER IF NOT EXISTS forward_runs_no_delete BEFORE DELETE ON forward_runs "
            "BEGIN SELECT RAISE(ABORT, 'append-only table forward_runs rejects DELETE'); END"
        )
        changed = " OR ".join(
            f"NEW.{column} IS NOT OLD.{column}" for column in _RUN_IDENTITY_COLUMNS
        )
        statements.append(
            "CREATE TRIGGER IF NOT EXISTS forward_runs_outcome_once BEFORE UPDATE ON forward_runs "
            f"WHEN OLD.status <> 'running' OR NEW.status NOT IN {_RUN_STATUSES} OR {changed} "
            "BEGIN SELECT RAISE(ABORT, 'forward runs may only record their outcome once'); END"
        )
        return statements
    raise ValueError(f"Unsupported registry dialect for append-only triggers: {dialect}")


def drop_append_only_statements(dialect: str) -> list[str]:
    """Return DDL that removes the triggers, for Alembic downgrades."""
    if dialect == "postgresql":
        statements = [
            f"DROP TRIGGER IF EXISTS {table}_{suffix} ON {table}"
            for table in (*EVIDENCE_TABLES, "forward_runs")
            for suffix in ("append_only", "no_truncate")
        ]
        return [
            *statements,
            "DROP FUNCTION IF EXISTS forward_protect_run()",
            "DROP FUNCTION IF EXISTS forward_reject_mutation()",
        ]
    if dialect == "sqlite":
        names = [
            f"{table}_no_{operation}"
            for table in EVIDENCE_TABLES
            for operation in ("update", "delete")
        ]
        names += ["forward_runs_no_delete", "forward_runs_outcome_once"]
        return [f"DROP TRIGGER IF EXISTS {name}" for name in names]
    raise ValueError(f"Unsupported registry dialect for append-only triggers: {dialect}")
