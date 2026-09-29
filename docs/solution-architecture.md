# EDGAR-MoE solution architecture

This is the single entry point to how EDGAR-MoE is designed, deployed, secured,
operated, and verified. It summarizes and links the detailed documents rather
than repeating them. Every claim separates what the code enforces from what
depends on provider configuration or operator action.

- **Status date:** 2026-09-29.
- **Live system:** [edgar-moe.vercel.app](https://edgar-moe.vercel.app), its [API reference](https://edgar-moe.vercel.app/api/docs), and a plain-language [architecture page](https://edgar-moe.vercel.app/architecture) for visitors.
- **Decision log:** [34 architecture decision records](adr/README.md), one proposed.
- **Detailed views:** [architecture and data flow](architecture.md), [forward-testing operations](forward-testing.md), [improvement plan](architecture-roadmap.md).

## 1. Context and goals

EDGAR-MoE asks whether SEC filings help predict stock returns. It reads each
10-K and 10-Q filing: the text, the XBRL financial statements, and the market
backdrop. A regime-gated mixture of experts then scores how the stock should
perform over the next 20 trading sessions, relative to the market.

The system has two lives:

1. **A historical study record (frozen v1).** The research record is retained,
   but its source-backed results are currently withheld from the deployed
   application while source-use and redistribution review remains open. Related
   reports may still exist in repository files, Git history, artifacts, or prior
   deployments; the synthetic-demo change is not a full takedown or legal clearance.
2. **A private ongoing prospective test.** A scheduled runner scores new filings
   with the same frozen model. It saves each forecast before the stock can trade,
   and appends each outcome only after 20 sessions. Nothing can be adjusted with
   hindsight. These outputs are currently withheld from the public app while
   source-rights review remains open ([ADR 0034](adr/0034-synthetic-public-boundary.md)).

It is research software. It never places orders, gives advice, or assesses
suitability.

### Stakeholders and concerns

| Stakeholder | Main concern | Where it is addressed |
| --- | --- | --- |
| Visitor or reviewer | Is this real evidence or only a demo? | The global synthetic-data disclosure, the Audit page, and [§8](#8-verification-and-traceability) |
| Quant researcher | No look-ahead, honest selection, costs included | [Research contract](../README.md#research-contract), [model card](model-card.md), and [ADR 0017](adr/0017-post-v1-selection-protocol.md) |
| Maintainer (operator) | Cheap, recoverable, observable operation | [§6](#6-reliability-and-operations), [runbooks](#runbooks), and [operator evidence](operator-evidence.md) |
| Data providers | Terms of use and redistribution | [Public-surface review](public-surface-review.md) and the `data-provenance.json` manifest |

### Quality priorities

When they conflict, the priorities apply in this order:

1. evidence integrity;
2. understandable failure behavior;
3. reproducibility;
4. affordable operation;
5. usable presentation.

A missed forecast is always preferable to a backdated one.

### Constraints

- **Cost:** zero recurring spend within free allowances. Paid services need explicit approval.
- **Team:** a single maintainer, with no on-call rotation.
- **Data:** free sources only (SEC EDGAR, the Alpaca IEX feed, FRED/ALFRED), with their gaps disclosed in the [data card](data-card.md).
- **Scheduling:** GitHub Actions schedules are best-effort. In practice, forward runs start 4–5 hours after their cron time ([#156](https://github.com/hoangnguyen2003/edgar-moe/issues/156)).
- **Immutability:** frozen v1 is never retrained. Any change creates a new version, evaluated prospectively ([ADR 0002](adr/0002-freeze-v1-prospective-evaluation.md)).

## 2. Requirements

### Functional

| ID | Requirement |
| --- | --- |
| F1 | Build a point-in-time dataset from filings, XBRL facts, prices, and macro vintages. |
| F2 | Select a model on pre-test folds, freeze it, and evaluate the locked test once. |
| F3 | Backtest a cost-aware, market-neutral long-short portfolio. |
| F4 | Publish a synthetic software demo through a public, read-only site and API while historical source rights are reviewed. |
| F5 | Record prospective forecasts before entry, and settle outcomes after 20 sessions. |
| F6 | Expose the frozen identity, public-data boundary, and control status for audit. |
| F7 | Offer an operator-run AI copilot that explains evidence with verifiable citations. |

### Quality-attribute scenarios

Each scenario gives a stimulus, the required response, and the current evidence.

| Attribute | Scenario and required response | Evidence today |
| --- | --- | --- |
| Integrity | A forecast is attempted at or after its entry time. It is refused; zero such forecasts are accepted. | Enforced in code and tested ([§8](#8-verification-and-traceability), R2) |
| Integrity | Any client issues `UPDATE`, `DELETE`, or `TRUNCATE` on an evidence row. The database rejects it. | Triggers installed in production ([ADR 0016](adr/0016-database-append-only-triggers.md)) |
| Reproducibility | A deployment serves a snapshot different from the reviewed one. The API refuses to serve it, and the deployment gate fails. | Runtime lock check ([ADR 0004](adr/0004-runtime-snapshot-lock.md)), plus a smoke check that compares identities on every production deploy |
| Availability | The private registry database is unreachable. The synthetic public demo still loads; runner/auditor operations fail independently, and the public API never probes or exposes database state. | Public API boundary tests and private failure scenarios; see the [failure scenarios](architecture-roadmap.md#failure-scenarios-to-exercise) |
| Security | The public serving tier is compromised. Its function has no registry connection, source credentials, or write path; private runner/auditor credentials remain in separate workflows. | Enforced in code ([ADR 0034](adr/0034-synthetic-public-boundary.md)); private database least privilege is separately verified |
| Security | A credential is committed or bundled. The bundle validator and secret scanning block or flag it. | Enforced in CI; push protection is enabled |
| Timeliness | A scheduled run starts late. Its margin before the open is recorded, with a warning below 90 minutes. | Enforced ([ADR 0020](adr/0020-pre-open-schedule-margin.md)); manual read-only observers verify a selected dispatch and summarize historical scheduled-time, creation-time, and start-time delays. Punctuality and scheduler origin are not guaranteed by GitHub metadata. |
| Research honesty | Rights review is unresolved, or a future public performance estimate is based on too few settled outcomes or filing months. Today the page withholds registry results entirely; any future publication must re-establish rights approval and the independent calendar-cluster gates. | Current hold enforced by [ADR 0034](adr/0034-synthetic-public-boundary.md); private metric uncertainty is specified by [ADR 0027](adr/0027-clustered-forward-rank-ic-uncertainty.md) |
| Recoverability | The registry is lost. It can be restored into an isolated target and reconciled with R2 evidence. | Rehearsed locally and in CI. **Pending:** a provider-side drill; the candidate RPO (24 h) and RTO (4 h) are unverified. |
| Performance | A change adds weight to the site. The first visit still downloads under 130 KB of compressed HTML, JavaScript, and CSS, and CI fails when it would not. | Enforced ([`check_web_budget.py`](../scripts/check_web_budget.py), budget in [`config/web_page_weight_budget.json`](../config/web_page_weight_budget.json)); measured at 97 KB on 2026-09-23 |
| Cost | A copilot tool loop or provider outage runs long. Wall-clock and context budgets stop it before the next provider call. | Enforced ([ADR 0008](adr/0008-copilot-run-execution-budget.md), [ADR 0009](adr/0009-copilot-context-budget.md)) |

## 3. Architecture views

### System context

```mermaid
flowchart LR
  visitor([Visitor or reviewer]) -->|HTTPS GET| system[EDGAR-MoE]
  maintainer([Maintainer]) -->|pull requests and manual workflows| system
  system -->|filings and XBRL facts| sec[(SEC EDGAR)]
  system -->|daily bars and corporate actions| alpaca[(Alpaca market data)]
  system -->|macro series and vintages| fred[(FRED / ALFRED)]
  system -.->|operator-run copilot only| llm[(LLM provider)]
```

### Containers and trust zones

```mermaid
flowchart TB
  subgraph public["Public zone: anonymous and read-only"]
    web[React app<br/>Vercel CDN]
    api[FastAPI, GET only<br/>Vercel function]
  end
  subgraph private["Private zone: holds write credentials"]
    runner[Forward runner<br/>scheduled GitHub Actions]
    research[Research pipeline<br/>maintainer workstation]
  end
  subgraph stores["Evidence stores"]
    snapshot[(Synthetic public fixture<br/>and lock, in Git)]
    archive[(Historical research archive<br/>not served by application)]
    registry[(Postgres registry<br/>append-only triggers)]
    r2[(R2 evidence mirror<br/>content-addressed)]
  end
  subgraph assurance["Assurance: credential-free or read-only"]
    ci[CI, CodeQL, and bundle checks]
    smoke[Deployment smoke check]
    auditor[Go evidence auditor]
  end
  web --> api
  api --> snapshot
  research -->|private research| archive
  research -->|reviewed PR| snapshot
  runner -->|writer role| registry
  runner --> r2
  auditor --> registry
  auditor --> r2
  ci -->|gates merge to main| snapshot
  smoke -->|verifies served identity| api
```

The separation ([ADR 0001](adr/0001-separate-serving-and-batch.md), constrained
by [ADR 0034](adr/0034-synthetic-public-boundary.md)) is between two tiers:
- The **public tier** serves a synthetic fixture only, with no database
  connection, source credentials, write credentials, or training stack.
- The **private tier** holds source and database write credentials, and runs
  only as reviewed code in scheduled or manual workflows.

### The forward cycle

```mermaid
sequenceDiagram
  participant GH as GitHub schedule
  participant R as Forward runner
  participant S as Sources
  participant DB as Postgres registry
  participant O as R2 mirror
  GH->>R: start (Tue–Sat, best-effort)
  R->>R: verify frozen model hashes
  R->>S: refresh filings, facts, and prices up to the cutoff
  R->>R: build point-in-time dataset (fails on any look-ahead)
  R->>DB: append forecasts where accepted ≤ forecast < entry
  R->>O: mirror content-addressed evidence
  R->>DB: append matured 20-session labels (never edits a forecast)
  R->>DB: record quality checks, including the pre-open margin
  R->>R: write the 5-session diagnostic (labeled supplementary)
```

The database and the object store are separate commits, not one distributed
transaction. A mirror failure leaves committed forecasts and a failed run, which
[`forward-reconcile-artifacts`](forward-testing.md) repairs by ID and hash.
Evidence is never erased to make a retry look clean.

### Deployment

| Component | Runs on | Trigger | Credentials | Trust |
| --- | --- | --- | --- | --- |
| React app | Vercel CDN (static `public/`) | Merge to `main` | None | Public |
| FastAPI | Vercel Python function, `sin1` | Merge to `main` | None; no registry connection | Public, read-only |
| Forward runner | GitHub-hosted Ubuntu 24.04 | Cron `17 7 * * 2-6` UTC, or manual | Writer DB, R2, and sources, each scoped per step ([ADR 0018](adr/0018-workflow-supply-chain.md)) | Private |
| Optional scheduler observer | GitHub-hosted Ubuntu 24.04 | Manual `workflow_dispatch` with a run ID | GitHub `actions: read` and `contents: read` only | Read-only evidence |
| Optional scheduler-lateness measurement | GitHub-hosted Ubuntu 24.04 | Manual `workflow_dispatch` with a bounded lookback | GitHub `actions: read` and `contents: read` only | Read-only evidence |
| Optional diagnostic-history collector | GitHub-hosted Ubuntu 24.04 | Manual `workflow_dispatch` with paired forward run IDs and artifact names | GitHub `actions: read` and `contents: read` only | Redacted research evidence |
| Registry | Managed Postgres (Neon) | — | Writer (runner); reader (auditor only) | Private evidence store |
| Evidence mirror | Cloudflare R2 | — | Runner write; auditor read-only | Evidence store |
| CI | GitHub Actions | Every PR and push | `contents: read` | Gate |
| Smoke check | GitHub Actions | Each production deploy | None | Gate |
| Container image | Built and healthchecked in CI | Every PR | None | Portability option, not deployed |

## 4. Data architecture

- **Layers:** raw → interim → processed → artifacts → public snapshot. Each
  authenticated layer has a manifest with source identity, row counts, and SHA-256
  digests ([storage layers](architecture.md#storage-layers)).
- **Point in time:**
  - A feature is valid only if it was available when the filing was accepted.
  - XBRL facts use the SEC filing timestamp, not the fiscal period end.
  - Market features stop at the previous completed session.
- **Identity:** the frozen v1 identity is one tuple: model, dataset, selection
  hash, locked-test hash, artifact digests, and snapshot lock. Changing any member
  makes a new version ([ADR 0002](adr/0002-freeze-v1-prospective-evaluation.md)).
- **Prospective evidence:** forecasts, labels, runs, quality checks, artifacts,
  and audit events live in Postgres. Evidence rows are append-only. Artifacts are
  content-addressed and mirrored to R2.
- **Diagnostic review:** a separate hash-pinned self-attestation can record a
  human review of one short-horizon history without rewriting it. It does not
  authenticate reviewer identity or authorize model changes ([ADR 0028](adr/0028-forward-history-review-attestations.md)).
- **Public boundary:**
  - The deployed app serves a generated synthetic fixture only. Historical
    research outputs and database-backed prospective records are withheld from
    the application while source-use and redistribution review remains open.
  - Raw provider data remains private. The remaining repository, Git-history,
    artifact, and prior-deployment copies are not thereby cleared or purged
    ([public-surface review](public-surface-review.md)).

## 5. Security architecture

| Threat | Mitigation | Residual risk |
| --- | --- | --- |
| The synthetic fixture is swapped or altered | Its lock is verified at build, at serving time, and against the reviewed commit on every production deploy. Its fingerprint is shown on the Audit page. | The Git host and maintainer account are trusted roots. Historical and prospective results are not served. |
| Forward evidence is edited or deleted | Database triggers, ORM guards, content hashes, the R2 mirror, and a read-only Go auditor. | A database owner can drop triggers with DDL. The mitigation is R2 retention, which is pending. |
| Forecasts are backdated | The `accepted_at ≤ forecast_as_of < entry_at` rule, a clock-skew bound, and the recorded pre-open margin. | A late scheduler reduces coverage but never permits backdating. |
| A credential leaks through the public bundle | The bundle validator rejects private runtime names, and secret scanning and push protection are on. Secrets are scoped to steps. | Human error is still possible; revoke and rotate per the [incident row](architecture-roadmap.md#failure-scenarios-to-exercise). |
| The serving tier is compromised | The public API is GET-only, serves only the synthetic fixture, and does not connect to Postgres or carry source credentials. | Provider operations still apply to private runner/auditor paths; see [§9](#9-risks-and-open-items). |
| Supply-chain compromise | Actions are pinned to commit SHAs, dependencies are locked (`uv --locked`, `npm ci`), and Dependabot and CodeQL run. | Upstream compromise between reviews. |
| Scraping or request floods | Paginated, bounded responses and CDN caching. | No application rate limit. Provider and CDN controls are pending. |
| Copilot prompt injection or exfiltration | Operator-run only, with allowlisted read-only tools, an exact provider host allowlist, no redirects, budgets, and citations bound to tool payloads. | Answers support review; they are not evidence. |

## 6. Reliability and operations

- **Degradation:**
  - Demo pages depend only on the packaged synthetic snapshot.
  - Public forward-data routes return `410` with `no-store` while source-rights
    review is open; this is a publication hold, not a registry outage.
  - An error while rendering a page is caught in the page area: the reader
    keeps the navigation and is told to reload or pick another page, instead
    of being left with a blank document. A page asset that a new deployment
    replaced is named as such, because reloading fixes it.
- **Observability:**
  - `/api/v1/governance` exposes the synthetic identity and the current
    publication holds. `/api/v1/forward/status` reports policy only; runner
    health is inspected through private operator evidence.
  - Each run appends quality checks, including the pre-open margin.
  - Redacted run artifacts are kept for 30 days.
  - An optional redacted webhook sends alerts.
  - Smoke reports carry correlation IDs.
  - A request identifier is returned only on responses a shared cache may not
    store. A cached read is served to everyone, so an identifier on it would
    belong to whoever filled the cache; it is left off rather than published
    misleadingly. Asserted on both sides in
    [`test_cache_policy.py`](../tests/unit/test_cache_policy.py) and
    [`test_smoke_deployment.py`](../tests/unit/test_smoke_deployment.py).
- **Service objectives:** these are proposed, not yet measured:
  - zero forecasts accepted after entry (an invariant);
  - a 96-hour freshness warning;
  - a candidate RPO of 24 hours and RTO of 4 hours, to be demonstrated by drills.

  See the [proposed objectives](architecture-roadmap.md#proposed-service-objectives--not-measured-commitments).
- **Cold starts:** Vercel serves the static bundle from its edge and invokes a
  small Python function only for uncached API reads. The public function does
  not connect to Neon, so it cannot wake the private registry or expose its
  contents. Historical hosted read observations describe the prior API
  boundary and are not a current production SLO.
- **Capacity:** `edgar-moe capacity-baseline` records latency, storage, and
  runtime, and marks provider quotas as unobserved rather than guessing. The
  [synthetic read-path benchmark](../scripts/benchmark_forward_read_path.py)
  isolates the cold calendar-interval CPU cost; [ADR 0030](adr/0030-forward-interval-read-path.md)
  records the optimization and why this local SQLite result is not a hosted SLO.
  A separate [bounded hosted observation](hosted-read-path-observation.md)
  records actual client latency, errors, and edge-cache mix without attributing
  network outliers to a particular layer or claiming an SLO. A secret-free,
  [low-rate daily observer](../.github/workflows/hosted-read-observation.yml)
  retains sanitized per-request evidence so a seven-day distribution can be
  assessed once seven consecutive UTC dates actually exist; serverless and
  database timing remain unobserved.

### Runbooks

- [Forward-testing operations](forward-testing.md): setup, the daily cycle, settlement, and reconciliation.
- [Restore rehearsal](restore-rehearsal.md): recovering into an isolated target, never over production.
- [Operator evidence packets](operator-evidence.md): recording provider-side observations without exposing secrets.
- [Evidence auditor](evidence-auditor.md): cross-checking registry rows against object bytes.
- [Research runbook](research-runbook.md): running the authenticated study end to end.

## 7. Cost and capacity

The design targets zero recurring spend:
- GitHub Actions uses free standard runners for this public repository.
- Serving is static assets plus one small read-only Python function, and the
  edge answers repeat snapshot reads, so most visits invoke nothing.
- The registry and evidence mirror hold small row and JSON volumes, sized for free allowances. Provider usage has not been measured yet.
- Market data uses the free IEX feed.

The costliest risks are bounded in code:
- runner compute: a 300-minute deadline;
- the runtime cache: at most the two newest entries ([ADR 0019](adr/0019-forward-cache-lifecycle.md));
- copilot provider usage: time and context budgets per run.

Provider allowances change, so they are checked before any paid or
larger-scale step.

## 8. Verification and traceability

Each requirement maps to its mechanism, where the mechanism is enforced, and
how it is verified. CI runs the linked tests on every pull request.

| ID | Requirement | Enforced in | Verified by |
| --- | --- | --- | --- |
| R1 | No look-ahead: features must be available when the filing was accepted | [`features/point_in_time.py`](../src/edgar_moe/features/point_in_time.py) | [`test_point_in_time.py`](../tests/unit/test_point_in_time.py) |
| R2 | Forecasts only before entry (`accepted_at ≤ forecast_as_of < entry_at`) | [`forward/registry.py`](../src/edgar_moe/forward/registry.py), [`forward/inference.py`](../src/edgar_moe/forward/inference.py) | [`test_forward_registry.py`](../tests/unit/test_forward_registry.py), [`test_forward_inference.py`](../tests/integration/test_forward_inference.py) |
| R3 | Evidence is append-only, even against direct SQL | [`forward/immutability.py`](../src/edgar_moe/forward/immutability.py), [migration `0002`](../migrations/versions/20260921_0002_append_only_triggers.py) | [`test_registry_immutability.py`](../tests/integration/test_registry_immutability.py) |
| R4 | The frozen v1 identity cannot drift | [`ops/frozen/SHA256SUMS`](../ops/frozen/SHA256SUMS), [`validate_frozen_runtime.py`](../scripts/validate_frozen_runtime.py) | [`test_frozen_runtime.py`](../tests/unit/test_frozen_runtime.py), in CI before every run |
| R5 | Production serves exactly the reviewed snapshot | [`api/repository.py`](../src/edgar_moe/api/repository.py), [`smoke_deployment.py`](../scripts/smoke_deployment.py) `--expect-lock` | [`test_snapshot_repository.py`](../tests/unit/test_snapshot_repository.py), [`test_smoke_deployment.py`](../tests/unit/test_smoke_deployment.py), and the [smoke workflow](../.github/workflows/deployment-smoke.yml) on each deploy |
| R6 | The public API cannot read or write the private registry | [`api/app.py`](../src/edgar_moe/api/app.py) (no registry connection; derived routes withheld) | [`test_forward_api.py`](../tests/integration/test_forward_api.py), [`test_smoke_deployment.py`](../tests/unit/test_smoke_deployment.py) |
| R7 | No secrets in the public bundle | [`validate_public_bundle.py`](../scripts/validate_public_bundle.py) | [`test_public_bundle.py`](../tests/unit/test_public_bundle.py), plus secret scanning |
| R8 | Workflow supply chain pinned and secrets scoped | [`.github/workflows/`](../.github/workflows/) ([ADR 0018](adr/0018-workflow-supply-chain.md)) | [`test_workflow_supply_chain.py`](../tests/unit/test_workflow_supply_chain.py) |
| R9 | The public app serves only synthetic results and withholds registry output | [`api/app.py`](../src/edgar_moe/api/app.py) | [`test_forward_api.py`](../tests/integration/test_forward_api.py), [`test_smoke_deployment.py`](../tests/unit/test_smoke_deployment.py) |
| R10 | Late runs are visible before the open | [`forward/workflow.py`](../src/edgar_moe/forward/workflow.py) ([ADR 0020](adr/0020-pre-open-schedule-margin.md)) | [`test_forward_workflow.py`](../tests/integration/test_forward_workflow.py) |
| R11 | Copilot citations are bound to real tool output | [`copilot/agent.py`](../src/edgar_moe/copilot/agent.py), [`copilot/verification.py`](../src/edgar_moe/copilot/verification.py) | [`test_copilot_agent.py`](../tests/unit/test_copilot_agent.py), [`test_copilot_verification.py`](../tests/unit/test_copilot_verification.py) |
| R12 | The first visit stays inside the page-weight budget | [`check_web_budget.py`](../scripts/check_web_budget.py), [`config/web_page_weight_budget.json`](../config/web_page_weight_budget.json) | [`test_web_budget.py`](../tests/unit/test_web_budget.py), and the bundle job in CI |
| R13 | The live point estimate retains its pooled definition; a time-clustered interval appears only after sufficient calendar history, otherwise a reason is explicit | [`forward/uncertainty.py`](../src/edgar_moe/forward/uncertainty.py), [`ForwardPage.tsx`](../apps/web/src/pages/ForwardPage.tsx), [ADR 0027](adr/0027-clustered-forward-rank-ic-uncertainty.md) | [`test_forward_uncertainty.py`](../tests/unit/test_forward_uncertainty.py), [`test_forward_api.py`](../tests/integration/test_forward_api.py), [`ForwardPage.test.tsx`](../apps/web/src/pages/ForwardPage.test.tsx), and the production deployment smoke contract |

**Verify the live system yourself.** From a clone, with no credentials, run:

```bash
uv run python scripts/smoke_deployment.py https://edgar-moe.vercel.app \
  --expect-lock config/public_snapshot.lock.json
```

This checks the security headers, disclosure files, and API health. It also
checks that the served frozen identity equals the lock reviewed in this
repository.

## 9. Risks and open items

The code is in place for every row below; what remains is operational. Each
item needs provider access or a maintainer decision, so the repository cannot
close it alone.

| Item | Why it matters | Next step | Owner |
| --- | --- | --- | --- |
| Private reader-role evidence | The former API reader role was provisioned with [`provision-reader.sql`](../ops/postgres/provision-reader.sql) on 2026-09-22 and verified against production: SELECT on all eight forward tables, no write privileges, and every write or DDL probe denied. It is no longer used by public serving. | Retain a redacted report by running the [reader audit](../.github/workflows/provider-reader-contract-audit.yml) with the SELECT-only URL as a private GitHub secret | Maintainer |
| Provider restore drill | The RPO and RTO are unproven until a restore runs against the provider | Follow the [restore rehearsal](restore-rehearsal.md) into an isolated target, and record the evidence packet | Maintainer |
| Object-store failure exercise | Partial-write recovery is tested locally, not against the hosted target | Run the provider R2 audit after an injected failure | Maintainer |
| Scheduler lateness ([#156](https://github.com/hoangnguyen2003/edgar-moe/issues/156)) | Runs finish roughly 40–100 minutes before the open | Run the read-only historical lateness measurement and selected-dispatch observer to establish a baseline; then configure the `scheduler` environment, deploy and observe the optional [Cloudflare scheduler adapter](../ops/scheduler/README.md), retain the reports and application margin, and cut over in a separate PR. The observers do not prove scheduler origin and an earlier cron remains a research-design change | Maintainer |
| Alert delivery | Failures are recorded but not yet pushed to a person | Configure a webhook recipient and exercise the failure cases | Maintainer |
| Source rights and historical copies | The current app is synthetic-only, but older reports, Git history, Actions artifacts, and prior deployments may still contain derived research output; rights remain unresolved. | Complete the source-rights and retention inventory. Do not re-enable publication or claim full takedown until separately approved and verified ([ADR 0034](adr/0034-synthetic-public-boundary.md)) | Maintainer |
| Known v1 defects | Mixed-period fundamentals and over-regularized baselines | Disclosed and kept frozen. Later studies use [ADR 0015](adr/0015-xbrl-fact-selection-policy.md) and [ADR 0017](adr/0017-post-v1-selection-protocol.md). | Research |

The roadmap tracks each item with its acceptance evidence in the
[improvement plan](architecture-roadmap.md). The [architecture baseline](architecture.md#architecture-baseline--september-18-2026)
records what was deliberately not verified.

## 10. How the decisions fit together

Five decisions carry most of the design:

1. **Separate serving from batch** ([0001](adr/0001-separate-serving-and-batch.md)): the public tier stays cheap and credential-free.
2. **Freeze v1, and judge changes only prospectively** ([0002](adr/0002-freeze-v1-prospective-evaluation.md)): results cannot be tuned after the fact.
3. **Verify the snapshot at serving time and at every deploy** ([0004](adr/0004-runtime-snapshot-lock.md)): what visitors see is provably what was reviewed.
4. **Append-only evidence enforced in the database** ([0016](adr/0016-database-append-only-triggers.md)): integrity does not depend on application discipline.
5. **Make operational risk visible rather than hidden** ([0020](adr/0020-pre-open-schedule-margin.md), [0023](adr/0023-external-forward-scheduler.md), [0019](adr/0019-forward-cache-lifecycle.md)): late runs and cache growth are measured and bounded, with an optional external trigger staged behind an observed cutover.

The [ADR index](adr/README.md) lists all 34 decisions by theme, including one proposal.
