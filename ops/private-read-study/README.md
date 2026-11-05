# Separate personal-portfolio hosted read study (#290)

Status: **source preparation, not an active hosted study**. The new service and
observer do not reopen the expired #361 pilot, reset its ledger, or touch #156's
active scheduler. The public application continues serving its synthetic fixture
and withholding prospective research routes.

## Backlog choice

The November 5 review found three open issues:

| Issue | Remaining dependency | Effort / earliest completion |
| --- | --- | --- |
| #156 | Already activated finite observation; real forecast margins still required. | November 5–7 observations, then evidence review; continue separately. |
| #290 | A fresh isolated SELECT-only target and protected hosted service, provider attribution, seven real days and comparisons. | Bounded implementation/setup plus at least seven days; existing account access makes this the next practical candidate. |
| #292 | Historically available listing/security identifiers, source-version evidence and the complete research chain. | External historical evidence remains unavailable; duration cannot be honestly estimated. |

#290 is the quickest remaining issue to advance without acquiring a historical
data source. It cannot be closed immediately or by substituting local/synthetic
timestamps for seven hosted days. The completed work for #275/#288 is separate.

## Account observations and access boundary

On November 5, the official pinned Vercel CLI refreshed existing account
authentication. Fixed-target read-only API checks verified the separately named
private project's identity, Singapore region, deployment protection on **all**
deployments, and the owner's active **Hobby** plan. No application request,
deployment or project mutation occurred. Private account/project identifiers and
credentials are retained only in ignored owner-only preflight files.

After fresh owner authorization, fixed-target Neon metadata checks verified the
intended PostgreSQL 18 project in Singapore. The owner then supplied the current
organization Billing page: **Free Plan**, 1 GB storage, 100 compute hours and ten
branches per project. The organization-details API returned HTTP 404, so its
response is not used as plan evidence. Project inventory returned four existing
branches and four idle endpoints, three of them disabled.

Actual schema-only creation checks exposed two constraints that the general
branch count did not establish. Neon requires a read-write endpoint to load a
schema-only branch. With the required endpoint fixed at 0.25 CU and default
scale-to-zero, both documented schema-only initialization modes returned
`ROOT_BRANCHES_LIMIT_EXCEEDED`. The setup did not fall back to cloning data or
remove an existing branch. An attempted fresh empty project in the same bound
organization was also refused with HTTP 404. Current access therefore does not
establish a usable new isolated target. Fresh account access or an owner-created
empty Free-plan project is needed before continuing setup. No database
connection, successful resource creation, deployment or observation occurred.

## Proposed workload and hosting scope

For this personal portfolio, the proposed planning workload is **10,000 invented
forecast records and 5,000 settled labels**, representing 500 fictional issuers,
four filing events per year and five years of retained observations. This is a
declared capacity assumption, not measured user traffic or an alpha result.
Keep ordinary small expert dictionaries and the real status/page/fixed-filter/
performance methods; include the 5,000-pair uncertainty branch. The older
120k/60k wide-row local experiment remains a separate stress case, not a baseline
to pool with this profile.

The declared volume has now passed an actual disposable local PostgreSQL 18
check: 10,000 invented forecasts and 5,000 labels across twenty filing quarters,
using 10,706,944 bytes for public table/index relations. Half the fictional
issuers are labeled in every quarter, exercising the real uncertainty branch
across twenty nonempty calendar months. All four real application routes passed,
as did a second performance read. Fifteen SELECT query shapes have redacted
`EXPLAIN (ANALYZE, BUFFERS)` plans after planner statistics were prepared. Each
reader closed; postflight found zero reader sessions and unchanged fixture/schema.
Only the newly created local cluster was removed. These five route samples are
not a hosted observation, percentile baseline, provider-start/resume proof or SLO.
The source/hash-bound operational packet is retained in ignored owner-only
artifacts. Current included headroom must still be checked before live setup.

The intended deployment is a **new protected preview** in the existing separate
Vercel project, never a deployment from the public repository root or a production
alias change. A fresh synthetic-only Neon branch/database/reader must be bound
before activation. Existing databases, branches, roles, old deployments, P0
namespaces, records and retained copies stay unchanged. Do not copy provider
research rows into the study or reuse the expired pilot role/allowance. If that
isolated target is unavailable under current included allowances, stop before
creation; no paid plan, upgrade, fallback vendor or keep-warm loop is authorized.

## Implemented source boundary

`scripts/stage_private_read_study.py` stages a new ignored owner-only bundle. It
reuses the existing reviewed 15-file portable kernel/application closure from
Git commit `3077310593bdaa1f4ac0087d207d565936c73a6f` and its fixed SQL. It adds
the new FastAPI adapter, Singapore preview config and an explicit Python 3.12
version. The entire runtime dependency closure is pinned to `uv.lock` versions
and distribution hashes with `--require-hashes`. No credentials, target policy,
dotenv file, research dataset or deployment authority is included. Staging is
not deployment and does not prove current hosted compatibility.

The service receives only a dedicated SELECT-only native Postgres URL and new
study bearer. It rejects old pilot names, writer-environment fallback, poolers,
libpq environment overrides, unverified TLS and disabled channel binding. Both
hosted connections explicitly use the hash-pinned `certifi` CA bundle; a
serverless image does not need an operator-created `~/.postgresql/root.crt`.
No environment-selected CA path or weaker TLS mode is accepted. Its separately
bound policy fixes an absolute **168-hour** window and complete kernel target/
schema/fixture/SQL hashes. Before/after that window it refuses without a database
connection. All paths authenticate; no public health/docs/CORS route or arbitrary
query/SQL is exposed. Responses remain private and non-cacheable.

The reader uses the existing same-snapshot integrity/role/fixture checks and
actual application DTO/query methods. A second connection with the **same
SELECT-only credential** checks reader presence and disappearance. Every request
rolls back, closes its reader and observer, and returns success only after
absence is verified. Driver text, connection strings, rows and identities never
enter failure bodies or timing headers. Reader errors stop that process.
Connection-acquisition, integrity, service and whole-lifecycle times remain
separate; the observer's database-wakeup overhead is included in HTTP/lifecycle
time rather than hidden as query performance.

This is a different study profile, not the old P0 global admission controller.
GitHub serializes the observer; the dedicated database role must enforce two
connections and the kernel enforces statement/transaction bounds. The app's
single-process semaphore is **not** a deployment-wide HTTP/read ceiling. The
published 360-attempt traffic model is an observer budget, not a claim that all
authenticated callers share a persistent global counter. No old global-limit
claim is transferred to this profile.

## Observation and stop contract

The new workflow is dormant unless the explicit repository activation flag is
set. It receives only HTTP bearer/platform-bypass credentials, never any database
credential. Each hourly invocation makes at most two fixed-route requests, 30
seconds apart, rotating status/page/ticker-filter/performance. It does not retry
the first failure or backfill missing hours. The declared full week has 168
pairs (336 measurement requests) plus 24 separately reserved setup verification
reads. A failed probe or redacted receipt disables **only this observer** through
a separate job with no study/provider/database secrets. #156 is unaffected.

The probe retains status, body size, edge-cache category, policy/host hashes and
bounded numeric timings. It discards response bodies, redirect destinations and
driver messages. It refuses cached responses on the private no-store routes.
Vercel consumes `Vercel-CDN-Cache-Control` before sending responses to clients,
so the external probe validates the client policy and any forwarded CDN policy
without requiring that internal header. See [Vercel's cache contract](https://vercel.com/docs/caching/cdn-cache).

`process=first` means only the first admitted read in that app process. It is
**not** Vercel cold-start evidence. Provider runtime start-type evidence and
Neon pre/post endpoint-state evidence must be collected/correlated separately;
missing evidence remains `unobserved`. Do not infer cold/resume from latency or
the 30-second pause. The existing public synthetic cache baseline is a separate
environment/cohort and cannot supply missing private database-read days.

After seven actual consecutive dates, retain all failed/missing/duplicate slots,
separate cache/process/provider/route and commit cohorts, and compare p50/p95/p99,
sample sizes and transport/contract/semantic error rates with a matched local
baseline. Safe representative-volume plans must use the freshly audited reader
and fixed bounded EXPLAIN tool. Small samples can justify **no SLO decision**;
do not manufacture an alert threshold. Source/publication holds, frozen v1 and
append-only forward records remain unchanged.

No hosted observation, hosted representative-volume setup, provider attribution
or issue completion is claimed. The seven-day window and live configuration
remain unbound until the target, provider attribution and concrete setup pass.
