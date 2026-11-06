# Separate personal-portfolio hosted read study (#290)

Status: **isolated setup and free provider tracing verified; not an active hosted study**.
See [the November 6 free-plan recovery](FREE-PLAN-RECOVERY.md) for the verified
Hobby cold/warm metadata path, credential dependencies and alternatives reviewed. The new service and
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
organization was also refused with HTTP 404 under the saved access.

The owner then completed fresh organization-scoped sign-in. API checks confirmed
the Free plan and a separate empty PostgreSQL 18 project was created in Singapore,
with one endpoint fixed at 0.25 CU and default scale-to-zero. It contains the
invented fixture below and a dedicated SELECT-only reader with a two-connection
limit. Existing project data, roles, branches and endpoint settings stayed
unchanged. This new setup does not extend the old pilot.

Five native setup reads now passed all four real routes, including a repeated
performance read, using verified TLS and required channel binding. Postflight
confirmed unchanged schema/fixture and zero remaining reader sessions. An earlier
attempt refused when remote backend cleanup had not finished at the first
post-close check; that failure remains retained. The adapter now polls for up to
one second, with the observer query timeout still enforced, and returns success
only after absence is actually observed. Persistent sessions still refuse; no
application read or connection is retried. These are setup reads, not a hosted
HTTP observation week. The measurement policy and observer remain inactive.

Four further native setup reads captured fifteen parameter-free, redacted
`EXPLAIN (ANALYZE, BUFFERS)` plans for the real status/page/filter/performance
SELECTs. All four routes passed, the fixture/schema stayed unchanged, and the
postflight reader count was zero. These plans were warm (all shared-block reads
were zero); their execution times ranged up to 12.736 ms. They are database-plan
evidence, not hosted request latency percentiles or cold-start evidence.

A new, empty Vercel project on the existing Hobby account now runs the unbound
service in Singapore, protected on **all** deployments. It has a fresh application
bearer and platform automation bypass, but **no database URL or study policy**.
The first CLI deployment automatically became this new project's production
deployment; its alias is protected, and no existing project's alias changed.
Three setup requests verified platform sign-in (302), application authentication
(401), and refusal of authenticated unconfigured reads (503), with no-store
responses. The eventual study deployment must explicitly select preview.

The provider events API returned the three request records but no runtime log
entries for this setup. Function start type was **unobserved in that initial events-API check**.
The November 6 session-trace verification now supplies provider startup evidence
without the paid invocation-event query; see the recovery note above. The existing
OAuth CLI credential was refused when minting a project-scoped automation token
(403, `Cannot create tokens for this app.`). The local helper is prepared to take
a new temporary classic setup token through hidden Terminal input, request an
eight-day token for only this new project, verify access, and revoke only the
specifically named temporary bootstrap token. It does not enable the observer.
The project scope and expiry still require final inspection before any credential
is placed in the observation environment. No CLI refresh credential is exported.

Before activation, complete provider start/resume capture, bind a fresh whole-hour
168-hour policy and matching database-reader expiry, deploy the SELECT-only URL
only to the protected study service, and verify the HTTP path. The current reader
has only a provisional one-day setup expiry. There are **zero hosted measurement
days**; neither setup HTTP responses nor native route reads count toward the week.

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

The intended study deployment is a **new protected preview** in the new isolated
Vercel project, staged from the minimal service bundle. Its synthetic-only Neon
database/reader must be bound to the study window before activation. Existing
databases, branches, roles, old deployments, P0
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
**not** Vercel cold-start evidence. Provider startup evidence now comes from complete, matching free session traces,
with request-time boot/init distinguished from complete invocations without
startup. Neon endpoint metadata is captured in separate steps before/after the
read pair; its idle-to-active label is pair-level evidence. Missing evidence
remains `unobserved` and failed metadata stops this observer. Do not infer cold/resume from latency or
the 30-second pause. The existing public synthetic cache baseline is a separate
environment/cohort and cannot supply missing private database-read days.

After seven actual consecutive dates, retain all failed/missing/duplicate slots,
separate cache/process/provider/route and commit cohorts, and compare p50/p95/p99,
sample sizes and transport/contract/semantic error rates with a matched local
baseline. Safe representative-volume plans must use the freshly audited reader
and fixed bounded EXPLAIN tool. Small samples can justify **no SLO decision**;
do not manufacture an alert threshold. Source/publication holds, frozen v1 and
append-only forward records remain unchanged.

Live setup receipts and the active window, when enabled, are recorded separately.
Setup checks are not seven-day observations and do not establish issue completion.

## Synthetic performance preparation

A free-preview setup attempt returned HTTP 503 at `snapshot_postcheck_refused`.
The original 5,000-pair / 1,000-draw uncertainty bootstrap took longer than the
unchanged five-second idle-transaction limit on hosted compute. The separate
study adapter now supports a policy-pinned `study-preparation.json` containing
only the already public invented inputs. No final interval is packaged.

For the performance route, the **original frozen algorithm executes during the
HTTP request before either database connection opens**. Its existing bounded
process cache is then used by the real same-snapshot registry read. The kernel
must report exactly one cache hit and no cache miss; otherwise the adapter
refuses. The fixture, schema, SQL, DTO, bootstrap count/seed and all database
limits remain unchanged. Exact float/time/order equality with the SELECT-only
fixture was checked before deployment. Inputs are serialized during staging
rather than regenerated on Linux, avoiding platform `sin`/`cos` rounding drift.

`preparation_us` retains this CPU cost. It is included in `lifecycle_us` and
client HTTP time, and excluded from connection/query/snapshot time. Initial
performance preparation and subsequent cache reuse must be reported separately;
the lower snapshot service time must never be advertised as total latency.
The adapter adds preparation overhead even on warm requests. This is a distinct
instrumented synthetic cohort, not a claim that the original public application
has this optimization or that arbitrary changing datasets can be preloaded.

The matched native local baseline now also uses this adapter. It has 100 warm
samples per route plus four initial-route samples, a disposable PostgreSQL 18
Unix socket without TLS, and fifteen bounded SELECT plans. It must remain
separate from hosted HTTP/TLS/provider-start and database-resume cohorts.

The 168-hour policy may start on a UTC minute boundary. Hourly slots are still
relative to that absolute start; no missing slot is replayed. A manually
triggered setup verification run must be marked and excluded from scheduled
measurement totals, with its run ID retained.
