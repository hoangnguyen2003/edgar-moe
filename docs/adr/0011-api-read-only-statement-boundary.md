# ADR 0011: Bound the API database session

- Status: accepted
- Date: 2026-09-20
- Deciders: project maintainer

## Context

The deployed API uses a separate Postgres reader URL and a small per-instance
connection pool. A role grant remains the authoritative least-privilege
control, but the serving process should also fail closed when configuration is
wrong and should not hold a serverless invocation on an unbounded query.
SQLAlchemy's shared session helper commits at the end of every request, so a
read-only database transaction default is useful defense in depth rather than
relying only on route conventions.

## Decision

When the API constructs a Postgres `RegistryDatabase`, it passes
`default_transaction_read_only=on` and a bounded `statement_timeout` through
the driver connection options. The timeout defaults to 5,000 milliseconds and
is configurable from 100 through 600,000 milliseconds with
`EDGAR_MOE_REGISTRY_API_STATEMENT_TIMEOUT_MS`. SQLite keeps its local
compatibility behavior, and private writer/migration construction remains
unchanged unless a caller explicitly opts into these parameters.

## Consequences

- A misprovisioned API connection is less likely to mutate registry data, and a
  slow query is interrupted by Postgres rather than consuming an unbounded
  serving request.
- Provider role grants, provider connection limits, and the reader audit are
  still required; application connection options do not prove external least
  privilege.
- Queries that legitimately need more time require an explicit operator review
  and a bounded setting change, not an unbounded default.
- Local SQLite development remains available, but it does not emulate the
  Postgres statement timeout.

## Verification

Unit tests validate option construction, setting bounds, API wiring, and SQLite
compatibility. CI's disposable Postgres reader job and the manually triggered
provider reader audit remain separate evidence layers.
