# Offline shared read-admission candidate

Approval scope: offline implementation and synthetic tests only. This directory
does not change the existing private pilot, public API, scheduler, databases or
restore copies. It has no deployment configuration, cloud resource initializer,
credentials, cron, real reader or production authentication implementation.
Do not deploy the emulator fixture or connect these modules to a live service.

## Implemented boundary

The ledger binds one immutable SHA-256 pilot identity and target commitment to
fixed absolute start/expiry timestamps, exactly 168 hours apart. Limits are
hardcoded to 360 admitted reads total, 80 per UTC day and one active read across
all users of the same record. Admissions and their replay tombstones are committed
atomically before an executor can start a read. Failures consume the allowance;
denied requests do not create database reads or refund admissions. Counts and an
unconfirmed active read survive restarts. A backwards clock stops the pilot.
Valid refused requests persist the clock high-water mark without consuming read
allowance. Once expiry is observed it is permanently closed, even if the clock
subsequently moves backwards or the controller is reconstructed.

The SQLite adapter uses synchronous transactions and bound SQL parameters.
Missing, corrupt, differently bound or unavailable state refuses access. Ordinary
operations never initialize missing state. Explicit offline fixture initialization
refuses an existing record; creating a persistent Node fixture refuses an existing
file, including symlinks, and reopening refuses symlinks or files readable by
group/other users. There is no reset, rebind, refund or lease-expiry path.
Persisted records must round-trip exactly through the ledger's JSON serializer.
Duplicate keys, alternate number/escape encodings and padded records refuse before
any read, without normalizing or overwriting the contradictory stored evidence.
This is corruption/ambiguity detection, not cryptographic protection against an
actor who has write access to the controller store; future live permissions must
isolate that store from callers and the SELECT-only registry reader.

The executor owns authorization and the reader dependency; neither can be supplied
through request input. Only a hashed request ID and one of three fixed query
classes (`status`, `forecasts`, `performance`) are accepted. The trusted reader
must synchronously return two promises:

- `result`: resolves to exactly `true` only after the permitted read and identity,
  grants and fixture checks have successfully completed. Raw rows or driver error
  payloads are not success acknowledgements and are never returned by this module.
- `closed`: resolves to exactly `true` only after the underlying query/session
  has actually ended and its connection is closed. Returning a response, requesting
  cancellation, or reaching a deadline is not proof of closure.

Both must settle before the slot can be released. Exceptions, failed reads,
unconfirmed closure, and unavailable timing stop the pilot. The 10-second
executor deadline is a refusal deadline, **not** a claim that it can kill a
database query. A deadline leaves the reservation held and admission stopped,
even if completion arrives later. There is no automated reconciliation/bypass
endpoint. Actual SQL statement limits and
confirmed cancellation belong to a separately reviewed future reader integration.

Only fixed status/reason codes, counts and separated controller/read elapsed
times are returned. No auth material, user values, raw reader payloads, driver
messages or target identifiers enter receipts. These local timings are not
hosted latency, database connection timing, cold-start attribution or an SLO.

## Files and offline tests

- `ledger.mjs`: immutable policy, persistent counters/reservation, validation.
- `sqlite-store.mjs`: candidate SQLite Durable Object storage contract.
- `executor.mjs`: trusted-reader execution and minimal adapter factory. Its
  callable surface has no initialize, reset, completion or stop RPC. HTTP fetch
  always returns a fixed non-cacheable 404.
- `sqlite-fixture.mjs` and `concurrency-fixture.mjs`: Node SQLite fixtures only.
- `workerd-fixture.mjs`: explicitly synthetic local-emulator entry point. Its
  test-only initializer and hardcoded fake authentication must never be deployed.
- `executor-process-fixture.mjs` and `executor-process.test.mjs`: synthetic local
  executors in independent processes share one actual SQLite file. Tests verify
  competing reads, delayed connection-close acknowledgement and crash/restart
  without resetting the active slot or consumed allowance. No network/provider
  client or deployment authentication is present in this fixture.
- `recovery-drill-plan.md`: proposed scope, free-quota gates, preservation rules,
  actual managed-recovery measurements and stop conditions. It executes nothing
  and does not represent an approved or completed provider recovery.
- `study-plan.mjs`: pure offline preflight for a proposed 168-hour study: 336
  measurement reads, fixed query rotation, two reads 30 seconds apart per hour,
  and 24 reserved verification reads. No traffic runner or cron is provided.
  Its synthetic receipt auditor retains missing/failed slots and separates
  query/phase and controller/read timing cohorts. Fabricated provider categories,
  mixed identities, duplicates and credential-bearing extra fields are refused.
  All hosted/representative/provider evidence flags remain false. The 30-second
  pair delay is an offline proposal, not an approved live traffic parameter.

Node 24 is already available in this repository's development environment:

```sh
node --test ops/private-read-admission/admission.test.mjs ops/private-read-admission/executor-process.test.mjs ops/private-read-admission/study-plan.test.mjs
```

Tests use only new temporary SQLite files and in-memory fixtures. Separate
processes compete for one actual SQLite file. Tests also cover daily/total limits,
UTC rollover, duplicate requests/completions, expiry, corruption, storage failure,
transaction rollback, restart, clock rollback, malformed reader contracts,
timeout ambiguity, auth refusal and value-free output. No package install occurs.
The virtual study exercises all 336 proposed measurements plus 24 checks through
the actual SQLite ledger and refuses a 361st read before the synthetic reader.
Virtual timestamps do not establish seven elapsed observation days, workload
representativeness, provider categories or hosted latency.

An additional test exercises the actual local workerd SQLite transaction/RPC and
Durable Object eviction/reconstruction interface. It requires an **already
installed** local Miniflare 5 module; it never downloads one automatically:

```sh
OFFLINE_MINIFLARE_MODULE=/absolute/local/miniflare/dist/src/index.js \
  node --test ops/private-read-admission/workerd.test.mjs
```

The emulator listens only on loopback, uses fresh temporary persistence, disables
telemetry and remote request metadata, and installs an outbound handler that
refuses external fetches. The test asserts zero outbound requests. No account,
namespace, binding or secret is created at a provider. Without that explicit local
module path the SDK test is skipped, not reported as passed. This is emulator
contract evidence, not proof of deployed provider controls.

## Still required before live work

The factory is not wired into the existing Python read API. A future reviewed
integration must map **every** reader instance to exactly one approved namespace
and pilot identity, prevent caller-selectable namespaces, authenticate trusted
execution, bind the actual target and observe real backend closure. A local
transaction test cannot prove those deployment-wide facts. Do not relabel the
existing per-process pilot as globally bounded or extend its expired lifetime.

Separate permission and evidence are still required for account/free-quota
verification, any new control resource, bindings/secrets, hosting, representative
synthetic workload/setup writes, seven-day observation traffic and absolute expiry.
Do not use the old tiny recovery fixture as representative scale. Managed backups
and measured RPO/RTO remain separate requirements. Preserve all existing copies,
production, source credentials and the public boundary. Keep #361 and #290 open.
