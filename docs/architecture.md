# Architecture and data flow

## Invariants

1. Source payloads are immutable and content-hashed.
2. A feature is valid only when `available_at <= accepted_at` for its filing event.
3. Market features use observations through the previous completed session.
4. XBRL facts use the SEC filing timestamp, not the fiscal period end.
5. Model preprocessing is fitted independently inside each training fold.
6. The locked test is evaluated after the model family, features, costs, and constraints are frozen.
7. Historical endpoints read a derived snapshot; prospective endpoints read the registry. Neither needs raw licensed market data or training dependencies.
8. The one-time evaluator requires the exact walk-forward selection hash and refuses locked-artifact overwrites.

## Storage layers

- `raw`: cached source responses, ignored by Git.
- `interim`: parsed filings, security mappings, and normalized facts.
- `processed`: Parquet event/availability/return tables plus compressed feature arrays.
- `artifacts`: model states, preprocessors, experiment manifests, and reports.
- `demo`: small public snapshot consumed by FastAPI and React.

Each authenticated layer has a JSON manifest recording source identity, configuration, row counts, paths, and SHA-256 digests. Historical research pages use a validated snapshot. Forward Lab uses a Postgres registry containing forecast evidence, appended outcomes, and mutable run status. The public API exposes reads only; the private runner performs writes.

## Failure behavior

- SEC requests identify the application, run below the published maximum rate, retry transient failures, and cache filing HTML.
- Ambiguous CIK/security mappings receive a confidence status; low-confidence events are excluded from primary results.
- Missing text or fundamentals activate a modality mask rather than zero-valued evidence.
- Missing market sessions censor the primary label and appear in the attrition report; corporate-action records are retained for mapping and return-quality review.
- The weekly workflow builds a verification snapshot but does not publish a new production snapshot. Failure does not replace the committed public snapshot; consumers should inspect the separate freshness endpoint rather than assume a recent deployment means recent research data.

## Deployment boundary

Training uses PyTorch and Transformers outside the serving tier. The deployed FastAPI function contains no training stack; it serves historical snapshot JSON and prospective registry reads. The React application performs visualization and filtering but no model inference or order routing. A provider-neutral Docker image packages the same serving boundary for a future container host; it is non-root, healthchecked, and does not include the private forward runner or source-data credentials.

## Architecture baseline — September 18, 2026

This is a repository-backed description, not a certification of cloud account
settings. See the [decision record](adr/0001-separate-serving-and-batch.md) and
[improvement plan](architecture-roadmap.md). Provider quotas, backup configuration,
database grants, branch protection, and billing settings were not inspected.

### Context and requirements

The primary user is a researcher inspecting reproducible historical results and
timestamped prospective evidence. The maintainer operates ingestion and evaluation.
This is not a broker, order-routing service, or real-time trading system.

Priorities, in order: evidence integrity; understandable failure behavior;
reproducibility; affordable operation; usable presentation. A missed forecast is
preferable to inventing a pre-entry timestamp or revising a recorded prediction.
The budget objective is zero recurring spend within available allowances, not an
unconditional promise that hosting and data services remain free.

### Components and flows

```mermaid
flowchart LR
  User[Public browser] --> Web[Vercel React static bundle]
  Web --> API[Vercel FastAPI GET endpoints]
  API --> Snapshot[Packaged historical snapshot]
  API --> DB[(Postgres registry)]
  Maintainer[Trusted maintainer] --> Git[Git repository and CI]
  Git --> Web
  Git --> Runner[Scheduled GitHub Actions runner]
  Sources[SEC / Alpaca / ALFRED] --> Runner
  Frozen[Hash-pinned frozen model] --> Runner
  Runner --> DB
  Runner --> R2[(Private R2 evidence mirror)]
  Runner --> Diagnostic[Diagnostic workflow artifact]
  Auditor[Go read-only auditor] --> DB
  Auditor --> R2
```

| Component | Responsibility | Boundary / source of truth |
| --- | --- | --- |
| React + `public/` | Display research and operational status | Public by design; never store credentials here |
| `api/index.py`, `src/edgar_moe/api/app.py` | Snapshot and registry queries | No training or mutation endpoints; shared application DB abstraction |
| Postgres | Forecasts, labels, identities, runs, quality checks | Prospective record of truth; SQLite is the local/test alternative |
| `scripts/run_forward_cycle.py` | Refresh, build, forecast, settle, diagnose | Batch execution, not a web request |
| `ops/frozen/`, `config/forward.yaml` | Reviewed inference artifact and identities | Hash-pinned model; code review governs changes to the pins |
| Local artifacts + R2 mirror | Content-addressed evidence bytes | Mirrored identity checks; bucket access/retention still require account verification |
| Actions caches | Reusable filings, embeddings, model downloads | Performance optimization, not a backup |
| Optional alert webhook | Receives redacted failed-run and health classifications | Secret is runner-only; delivery is best-effort and never contains database/R2 credentials |
| Go evidence auditor | Cross-check registry rows against object bytes | Read-only operational boundary; no repair or write authority |

Production workflow sequence: restore caches and verify model → refresh inputs →
build dataset → commit pre-entry forecasts → settle mature outcomes → produce
five-session diagnostic. Forecasts and settlements have separate run records.
Twenty-session recorded labels remain the official evaluation; the diagnostic and
earliest-event audits are supplementary and must be labeled as such.

### Trust boundaries and integrity limits

- Browser → API: anonymous public reads. CORS is not authorization. Pagination
  bounds response sizes, but does not by itself prevent scraping or request floods.
- API → database: GET-only routes reduce exposure. The API now prefers
  `EDGAR_MOE_REGISTRY_READ_DATABASE_URL`; local SQLite compatibility falls back to
  the writer URL, while a missing reader URL never falls back to a hosted Postgres
  writer. The API reader also uses a bounded per-instance connection pool
  (`EDGAR_MOE_REGISTRY_API_POOL_SIZE`, `EDGAR_MOE_REGISTRY_API_MAX_OVERFLOW`, and
  `EDGAR_MOE_REGISTRY_API_POOL_TIMEOUT_SECONDS`) to limit serverless connection
  fan-out. Provider/project limits still require external verification. The shared
  database session helper can commit, so the deployed reader role must still be
  granted SELECT-only privileges and verified before claiming database-enforced
  least privilege.
- Runner → providers/database/R2: high-trust execution with source and write
  credentials. Workflow permissions are `contents: read`, but job secrets remain
  powerful. Review workflow and dependency changes as privileged code changes.
- External sources → features: input hashes and availability checks support
  provenance; they cannot guarantee upstream truth or eliminate every research bias.
- Application → evidence store: ORM listeners block normal evidence updates and
  deletes. Raw SQL and database administrators can bypass these listeners. Hashes
  detect mismatches against a trusted reference; they are not independent notarization.
- Database → object store: these are separate commits, not one distributed
  transaction. Forecast rows are appended before batch evidence storage. A mirror
  failure may leave committed forecasts and a failed run. Reconcile by IDs and
  hashes; never erase evidence to make a retry look clean.

### Deployment and operations

`vercel.json` builds the React application into the deterministic `public/`
output directory, validates the deployment contract and publishable bundle,
packages the Python API, and requests `sin1`. CI separately rebuilds the React
assets and checks that committed assets match source. The Vercel build is
therefore self-contained, but it must not be assumed to wait for CI unless
account settings enforce that gate.

The forward workflow pins Ubuntu 24.04, runs Tuesday–Saturday at 07:17 UTC,
serializes production jobs, and has a 300-minute compute deadline inside a
360-minute job. The application reports a 96-hour freshness window. These are
configured behaviors, not guaranteed scheduler latency or availability SLAs.

Forecast list/performance routes request 60-second caching; registry status is
`no-store`. An unconfigured registry is reported explicitly; database errors
produce an unavailable status or 503 on forward data routes. Historical snapshot
reads have a separate dependency path. Snapshot health alone does not prove that
the registry or scheduler is healthy.

No independently verified restore drill, end-to-end alert delivery, cloud-role
audit, or capacity benchmark is established by this baseline. The checked-in
reader-role grant contract and verifier make least-privilege verification
repeatable, but no provider grant has been independently verified yet. The
[restore-rehearsal runbook](restore-rehearsal.md) defines an isolated recovery
procedure, but no restore result has been recorded. The roadmap defines the
evidence needed to close those gaps without adding unnecessary services.
