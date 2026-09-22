# ADR 0019: Bound the forward-runtime cache lifecycle

- Status: accepted
- Date: 2026-09-22
- Deciders: project maintainer

## Context

The forward runner needs resumable filing, FinBERT, and Hugging Face caches. The
cache key includes the run ID and attempt so a timed-out cycle can save partial
work without mutating an existing cache. That also means every run can add a
large cache entry. GitHub's repository cache quota is finite, and evictions of
unrelated `uv`, npm, or Go caches make CI slower without changing evidence
correctness.

The production cycle owns registry and object-store credentials, so granting its
job `actions: write` solely to delete caches would widen the blast radius of a
runner or dependency compromise.

## Decision

- Keep the unique run-attempt key and prefix restore behavior. Resumability and
  immutable cache entries take precedence over overwriting a live cache.
- Add a dependent `prune-forward-runtime-caches` job after the cycle. It has no
  checkout, third-party actions, dependencies, or production secrets.
- Give only that job `actions: write`; it has no repository-content permission. It lists all
  repository caches, selects the `forward-runtime-*` family, retains the two
  newest entries by creation time, and deletes older entries by cache ID.
- Run cleanup even when the cycle fails after a cache save, but not when the
  workflow is cancelled. Cleanup errors fail visibly so operators can retry or
  investigate instead of silently accepting quota growth.

## Alternatives considered

- A stable cache key would avoid accumulation but cannot replace an existing
  cache and would make partial-cycle state harder to reason about.
- Deleting caches from the credential-bearing cycle job would be simpler but
  violates the workflow supply-chain boundary in ADR 0018.
- Retaining only one entry saves more quota, but two entries provide a rollback
  point if the newest cache is incomplete or corrupt.

## Consequences

The cache family is bounded to at most two entries after a successful cleanup,
reducing eviction pressure and preserving a recent cold-rebuild fallback. A
temporary GitHub API failure can leave extra caches until the next run or manual
rerun, but it cannot alter forecasts or evidence. Cache contents remain
recomputable performance state and are never treated as a backup.

## Verification

The workflow contract test checks the dependency, cancellation guard, exact
permission scope, GitHub token source, prefix filter, two-entry retention rule,
and absence of secrets in the cleanup job. CI parses the complete workflow and
the production run can be audited from the cleanup step's deletion summary.
