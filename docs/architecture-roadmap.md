# Architecture improvement plan

Baseline: 2026-09-18. This is a proposed backlog, not a claim of completed controls.
The maintainer owns decisions and operational verification; coding assistance can
prepare implementations, tests, and runbooks. Cloud changes require explicit
scope and access. No paid services are authorized by this plan.

## Priorities and acceptance evidence

| Priority | Work item | Why / acceptance evidence |
| --- | --- | --- |
| P0 | Database least privilege | Application support now separates the API reader URL from the private writer/migration URL and refuses to use a hosted Postgres writer as an API fallback. The checked-in provisioning SQL and reader-role verifier define the remaining operational contract. Still required: run them against the deployed Postgres instance and retain the redacted verification output; disposable Postgres tests proving the reader cannot INSERT/UPDATE/DELETE or change schema are still a follow-up if provider staging is available. |
| P0 | Restore rehearsal | Follow the [restore-rehearsal runbook](restore-rehearsal.md): restore to an isolated target, verify schema and table counts, run the Go evidence auditor, exercise the local read path, and record elapsed time and missing data. Never rehearse by overwriting production. |
| P0 | Partial-write reconciliation | The scheduled runner now has an optional post-cycle Go audit using a separate SELECT-only database role and read-only R2 token; audit JSON is retained and audit findings fail the workflow without deleting evidence. Still required: configure those roles, inject a production-like object-store failure, demonstrate failed-run visibility and missing evidence, and record a safe retry/reconciliation without changing original forecasts. |
| P1 | Alert delivery | The runner now supports an opt-in HTTPS webhook with redacted payloads and explicit failed-run, stale-runner, registry-unavailable, quality-failure, and quality-warning classifications; delivery is non-blocking and disabled when no secret is configured. Still required: configure an agreed recipient, trigger controlled failure/status cases, and retain delivery evidence. UI status and retained artifacts are not notification. |
| P1 | Release gates | Use task branch → PR → green CI → self-review → merge. Branch protection is optional for this personal private repository; the lightweight PR policy is the current cost-free control. Verify deployment ordering so committed public assets cannot be released while required validation fails. |
| P1 | Public surface review | Review source-data redistribution rights, exposed forecast fields and error messages, abuse limits, and secret scanning. Static JavaScript is public; secrets must never enter its build inputs. |
| P1 | Cost and capacity baseline | Record cold/warm job runtime, cache size, API latency, DB connections, object storage, and account quotas. Set a documented stop/approval threshold before paid usage or larger experiments. |
| P2 | Research drift observability | The `research-drift` command now compares the frozen training dataset with a later prospective dataset, including missingness/distribution statistics and hash-pinned frozen component outputs. It records a content hash and explicitly avoids targets, labels, registry state, and automatic retraining. Still required: run it on a representative later dataset, review threshold policy, and retain the report with the corresponding forward-cycle evidence. |

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
