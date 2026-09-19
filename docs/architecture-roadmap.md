# Architecture improvement plan

Baseline: 2026-09-18. This is a proposed backlog, not a claim of completed controls.
The maintainer owns decisions and operational verification; coding assistance can
prepare implementations, tests, and runbooks. Cloud changes require explicit
scope and access. No paid services are authorized by this plan.

## Priorities and acceptance evidence

| Priority | Work item | Why / acceptance evidence |
| --- | --- | --- |
| P0 | Database least privilege | Application support now separates the API reader URL from the private writer/migration URL and refuses to use a hosted Postgres writer as an API fallback. The checked-in provisioning SQL, reader-role verifier, and disposable PostgreSQL CI service job prove SELECT-only grants plus denied UPDATE/DELETE/DDL probes. Still required: run them against the deployed Postgres instance and retain the redacted verification output; provider-specific grants and connection limits remain operational work. |
| P0 | Restore rehearsal | A safe local SQLite rehearsal now copies into a new file, compares every forward-registry table count, and exercises the API read path. CI now adds a disposable PostgreSQL dump/restore drill with deterministic counts, Alembic verification, the independent Go auditor, and a restored read-path probe; it retains redacted evidence for 30 days. Still required: follow the [restore-rehearsal runbook](restore-rehearsal.md) against an isolated provider target and record provider backup/restore timing and missing data. Never rehearse by overwriting production. |
| P0 | Partial-write reconciliation | The scheduled runner has an optional post-cycle Go audit using a separate SELECT-only database role and read-only R2 token. Mirrored writes now retain the verified primary reference when the mirror fails, mark the run failed without changing forecasts, and let `forward-reconcile-artifacts --repair` recover the mirror; integration tests inject the outage and retry idempotently. Still required: configure those roles, exercise the production-like object-store failure against the hosted target, and retain the provider audit/retry evidence. |
| P1 | Alert delivery | The runner now supports an opt-in HTTPS webhook with redacted payloads and explicit failed-run, stale-runner, registry-unavailable, quality-failure, and quality-warning classifications; delivery is non-blocking and disabled when no secret is configured. Still required: configure an agreed recipient, trigger controlled failure/status cases, and retain delivery evidence. UI status and retained artifacts are not notification. |
| P1 | Release gates | Use task branch → PR → green CI → self-review → merge. Branch protection is optional for this personal private repository; the lightweight PR policy is the current cost-free control. Verify deployment ordering so committed public assets cannot be released while required validation fails. |
| P1 | Public surface review | The public-surface review now records the derived-data boundary, field/error exposure, bounded anonymous queries, security headers, and CI/Vercel bundle validation. Still required: confirm source/data redistribution terms for the deployed snapshot and configure provider/CDN abuse controls; the application does not pretend that anonymous serverless reads are rate-limited by Python alone. |
| P1 | Cost and capacity baseline | `edgar-moe capacity-baseline` now records local snapshot/read-path latency, registry probe latency, filesystem/cache size, disk headroom, and optional observed workflow runtime while explicitly marking provider quotas, managed DB connection counts, and object-storage usage as unobserved. The scheduled/manual hosted-capacity workflow now retains a weekly runner baseline and can probe an optional public API via `EDGAR_MOE_CAPACITY_API_URL`. Still required: configure the API variable if desired, record provider measurements, and review the documented stop/approval thresholds before paid usage or larger experiments. |
| P2 | Research drift observability | The `research-drift` command now compares the frozen training dataset with a later prospective dataset, including missingness/distribution statistics and hash-pinned frozen component outputs. A representative run now compares `research-2026-07-31-3553e7ad78dd` (5,961 events) with `research-2026-08-06-e4ba3c323d37` (6,039 events): 807 feature columns and 9 frozen components were stable, with zero warnings or dimension mismatches. The content-hashed evidence is [`reports/research-drift-2026-08-06.json`](../reports/research-drift-2026-08-06.json), hash `0899bdfb…`. Still required: review thresholds over multiple later datasets and retain each report with its corresponding forward-cycle evidence; this single target-free comparison is not a performance or retraining decision. |

P0 means highest next-work priority, not a confirmed active incident. Current role
grants, backups, provider alert settings, and billing controls are unknown until
verified. Provider allowances change; verify current account terms before any
capacity or cost decision.

## Failure scenarios to exercise

| Failure | Expected behavior | Recovery evidence |
| --- | --- | --- |
| Postgres unavailable | Forward status unavailable / data endpoints 503; historical snapshot still readable | API integration test plus isolated outage exercise |
| Source throttling or missing bars | Retry or explicit failure/quality evidence; no fabricated outcome | Fixture tests and inspectable failed run |
| Runner misses pre-entry window | Skip ineligible filings rather than backdate | Timestamp tests and zero-candidate report |
| R2 write fails after DB commit | Failed run may retain forecasts; no false atomicity claim | Injected failure and reconciler test |
| Cache disappears | Cold rebuild or visible deadline failure, never evidence loss by design | Cache-disabled staging run and runtime measurement |
| Database is lost | Documented isolated restore, then reconcile object references | Restore drill; R2 objects alone do not demonstrate full DB recovery |
| Credential exposed | Revoke/rotate affected credential and inspect use | Scoped incident checklist; no secret printed in logs or reports |

## Proposed service objectives — not measured commitments

- Integrity: zero forecasts accepted at or after their entry timestamp; verify
  every forecast path, including retries. This is an invariant, not an availability SLO.
- Freshness: preserve the existing 96-hour warning policy initially, then measure
  actual scheduled-cycle completion and pre-entry misses before tightening it.
- Recovery: start by testing a candidate RPO of 24 hours and RTO of 4 hours for
  registry loss. Do not promise either until backup cadence and restore drills
  demonstrate them. RPO is allowable lost data; RTO is time to restore service.
- Serving: gather a seven-day latency/error baseline before setting a percentile
  latency or availability target. Do not infer API health from the static homepage.

## How to use this as Solution Architecture practice

For each task, record: requirement → constraints → options → decision → failure
behavior → verification → cost → revisit condition. Explain which part is enforced
by code, which depends on infrastructure configuration, and which is only a goal.

The first application implementation is a least-privilege database role contract:
the API can receive a dedicated reader URL while the private runner retains the
writer URL. This reduces the impact of a serving-tier compromise without requiring
a new hosting provider. Production role grants, disposable Postgres tests,
migration procedure, restore/rollback steps, and an operator verification record
remain before the control can be called complete.
