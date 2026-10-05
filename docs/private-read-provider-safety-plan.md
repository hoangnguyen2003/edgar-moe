# Private read pilot: provider observations and safety plan

Status: **planning only; broader readiness blocked**, observed 2026-10-05.
Related issues: #361 (provider controls), #290 (hosted latency). This is not a
new deployment, a recovery approval, or source-use clearance. Keep the public
application, production, all three restored copies, the synthetic recovery
database, private test bucket, credentials and original evidence unchanged.

## Observed, not inferred

The maintainer supplied the isolated branch's compute overview, compute edit
panel and Backup & Restore page. Screenshots are retained privately with hashes;
their filenames provide capture times, not independently attested timestamps.
No values were saved and no restore or provider mutation was performed.

| Boundary | Actual observation | What it does not establish |
| --- | --- | --- |
| Isolated compute | Suspended; autoscaling 0.25–2 CU; scale to zero after 5 minutes idle | A dedicated pilot connection or invocation cap |
| Console connections | 894 direct connections / 10,000 pooled client connections displayed | 10,000 simultaneous queries or a role-specific budget |
| Earlier isolated SQL | `max_connections=901`, reserved counts 4 and 0; reader `rolconnlimit=-1` | Exact reconciliation with the later console display or a bounded reader role |
| Hosted app | One in-flight read and 60 admitted reads **per process**, no persistent pool, no retries | A total deployment request quota across instances/restarts |
| Backup & Restore | Six-hour history window with `production` selected as source; no snapshots or schedule displayed; snapshot creation shown as root-only | Recovery coverage for the synthetic database created later on the child branch |
| Restore timestamp field | 2026-10-05 11:21 GMT+7 selected in the form | A successful backup receipt, durable recovery-point timestamp, or measured RPO/RTO |

Keep the two numeric connection observations separate. Do not silently replace
the observed server value with a documentation table, subtract reserved counts
to invent an exact reconciliation, or label an untested pooler configuration
verified. [Neon's pooling documentation](https://neon.com/docs/connect/connection-pooling)
distinguishes client connections from backend connections and active
transactions; its shared pooling configuration is not user-configurable.

The earlier immutable deployment and 15-case smoke remain valid evidence for
their short-lived synthetic scope. They are not a seven-day latency study or a
managed-backup exercise. The approved app expiry stays **2026-10-05 06:53:41 UTC
(13:53:41 GMT+7)**; branch expiry is separate. No lifetime or budget reset is
authorized. Application expiry prevents new reads, not deletion of the service,
revocation of every credential, cancellation of an already admitted read, or
automatic preservation beyond the existing branch expiry.

## Decision: finish evidence review before expanding runtime scope

Retain the current pilot and allow its original expiry to stop new application
reads. Do not upgrade, add a database, seed representative data, change compute
size, schedule traffic, repeat recovery, or extend branch/app lifetime. Existing
free quotas are shared; $0 billing does not guarantee public-site availability
if a pilot exhausts them. [Vercel scaling](https://vercel.com/docs/functions/concurrency-scaling)
can create additional instances, so a process semaphore is not a deployment-wide
budget. Keep this distinction explicit rather than claiming a platform cap from
the application's local limits.

For a longer private read service, review these gates in order:

1. **Dedicated backend connection budget — proposed, not applied.** A small
   budget of two normal backend sessions for the existing isolated synthetic
   reader is a candidate: one read plus one diagnostic session. First verify
   the role is dedicated and its changes cannot affect another workload.
   Obtain explicit permission for an isolated role-setting change, a direct
   endpoint configuration and a small refusal test; never use a production
   owner connection. Keep identity binding, TLS verification, grants and the
   synthetic digest intact. PostgreSQL describes role connection enforcement
   as approximate and exempts superusers; a role cap is not an exact HTTP
   admission counter. See [CREATE ROLE](https://www.postgresql.org/docs/current/sql-createrole.html).
   A pooled client count is a different boundary: do not claim a backend role
   cap bounds all pooler clients or switch endpoint secrets silently.
2. **Deployment-wide admission — still undecided.** Specify total authenticated
   reads, maximum simultaneous reads, time window, overflow behavior and
   expiry before implementation. A verified existing platform control could
   provide this boundary only if available under the actual $0 account.
   Otherwise a shared, atomic admission store is a new architecture/write
   surface requiring separate approval and least-privilege separation from
   the registry reader. A counter in each function, operator-serialized test
   traffic, or a SQL connection cap must not be relabeled global admission.
   Do not introduce an unapproved writable store merely to make readiness pass.
3. **Bounded verification — only after scoped approval.** Independently inspect
   applied settings; exercise the intended reads and overflow refusal with
   a predeclared small request/connection allowance. Retain safe status/timing
   and identity/grant/digest receipts, not URLs, tokens, headers or rows.
   Confirm connections close, failures do not retry, and all preserved-copy
   counts and the complete synthetic digest remain unchanged. This is not a
   stress/load test and cannot establish representative SLOs.
4. **Stop/rollback.** Stop on auth/cache/grant/fixture drift, connection refusal,
   unexpected retries, unknown billable configuration, quota pressure or
   public-site impact. Current default is the original app expiry. Any stronger
   provider-side shutdown requires scoped permission and must affect only
   the pilot, without deleting records, restoring over a branch, reseeding,
   terminating unrelated sessions or changing source credentials. A future
   role-setting rollback must record its before/after value; restoring an
   unlimited value is not an approved safety fallback. Do not silently redeploy
   with a fresh expiry to recover availability.

## Managed recovery: keep the evidence gap honest

The selected production history cannot recover later synthetic-only changes by
itself. Do not click Restore on the preserved child branch. The logical restore
and partial-write exercises already passed; their elapsed times are not managed
PITR recovery times. No additional target, snapshot, retention change or drill
is authorized by this plan.

To prove managed recovery, first identify a permitted recovery source that
actually contains the intended database state, its effective retention window,
and a successful capture/recoverable-point receipt. A subsequent drill would
need explicit approval for a new isolated target that preserves every existing
copy. Predeclare dataset identity, latest confirmed durable point and failure
reference time for measured RPO; start the RTO clock at the recovery request and
stop it only after independent identity, grants, counts, content hashes and read
availability verify. Include provisioning, restore and verification time. If
the present child-branch configuration cannot supply this source, record the
unsupported/missing control; do not infer coverage from the parent or upgrade
without a cost/scope decision.

## Retained evidence and next decision

New private packet `provider-p0-private-read-limits-2026-10-05` contains 66
independently hash/redaction-verified artifacts, including both new observation
receipts. All 64 earlier retained artifacts were copied with matching hashes;
older packets remain immutable. Raw screenshots and account/connection material
are not published in Git or GitHub artifacts.

- Packet SHA-256: `c6dd64e2a17e06069591e2b09874370441fca8f7aa54a8924da5ec4dc21491e3`.
- P0 readiness SHA-256: `309888f8ea0600a6e24321afd0a9560d8afb98e1850926ce1f8959ff08b40491`.
- Decision: **blocked** on the broader `database_least_privilege` control;
  configuration observations are not proof of global admission or managed RPO/RTO.

Before provider changes, the maintainer must choose whether to authorize the
isolated backend cap/refusal test and how to enforce longer-lived global
admission and obtain managed recovery coverage under $0. Keep issues #361 and
#290 open. The latter still requires representative-volume query plans, seven
actual consecutive days and separated provider latency attribution; the current
tiny fixture and expiring branch cannot be substituted for that acceptance.
