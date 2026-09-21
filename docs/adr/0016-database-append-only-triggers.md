# ADR 0016: Enforce the append-only registry in the database

- Status: accepted
- Date: 2026-09-21
- Deciders: project maintainer

## Context

The forward registry's immutability was enforced by a SQLAlchemy `before_flush`
guard, and the SELECT-only API role could not write at all. Any other client of
the writer credential, including raw SQL, bulk ORM statements, or a future tool,
could still update or delete evidence. The forward-testing guide disclosed this
as ORM-only protection.

## Decision

Alembic revision `20260921_0002` installs database triggers on SQLite and
Postgres, defined once in `edgar_moe.forward.immutability`:

- evidence tables (datasets, models, forecasts, labels, artifacts, quality
  checks, and audit events) reject every `UPDATE`, `DELETE`, and, on Postgres,
  `TRUNCATE`;
- `forward_runs` rejects `DELETE` and `TRUNCATE`, and accepts an `UPDATE` only
  while the stored status is `running`, only to a known status, and only for
  outcome columns (`status`, `result_counts`, `error_message`, `finished_at`).

Postgres raises SQLSTATE `23001` (`restrict_violation`), which surfaces as an
integrity error. `RegistryDatabase.create_schema()` installs the same idempotent
triggers, so every test runs under the migrated write contract.

## Consequences

- Evidence can no longer be rewritten through the writer credential by
  accident or by a tool that bypasses the ORM.
- A database owner can still drop the triggers with DDL; object-store retention
  and the Go auditor remain the independent evidence controls.
- Changing the trigger DDL requires a new Alembic revision. A golden-hash test
  fails until that revision is added.

## Verification

SQLite integration tests reject raw updates, deletes, reopened runs, and
identity rewrites while allowing the running-to-terminal transition. The
migration test confirms that `alembic upgrade head` and `create_schema()`
install identical triggers and that downgrade removes them. On Postgres, the
same probes, the reader-role audit, and a full seed, dump, restore, audit, and
compare rehearsal passed locally, and all 16 triggers survived the restore.
