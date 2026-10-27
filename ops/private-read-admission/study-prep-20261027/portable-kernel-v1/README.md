# Portable read kernel — 2026-10-27

The actual Python read service now runs from a private source bundle in an
isolated minimal environment, without importing the development checkout,
starting Node or loading research packages. This removes a concrete packaging
dependency from the proposed #290 hosted path. It does not establish a deployed
service, admission/observer integration, representative volume or hosted days.

## Built and measured

The final source candidate is `bundle-07`. `proof-02` measured `bundle-05`;
the final candidate's **19 runtime/SQL/dependency files are byte-identical**.
The final builder reads checked-in, hash-pinned SQL text and the dependency lock
before writing its manifest. It reproduces the exact original SQL-generation
bytes without importing the old JavaScript preparation modules. The equivalence
receipt preserves that packaging distinction. It
contains **15 application source files** copied byte-for-byte from reviewed main
commit `3077310593bdaa1f4ac0087d207d565936c73a6f`, the new kernel and the fixed
SQL contract. The full `ForwardRegistry` implementation is retained rather than
replacing its read methods with simplified queries. Its import closure includes
shared registration/artifact definitions, but the kernel invokes only fixed
read methods under the separately audited SELECT-only role.

Six direct dependencies resolve to **12 hash-pinned distributions**. A new
CPython 3.12.13 macOS/arm64 environment installed them with `--require-hashes`;
the final run installed entirely from the package cache in offline mode. Package
index resolution/downloads are recorded separately from data-provider activity.
The original development environment was not changed.

Build with Python and Git, with the reviewed source commit present locally:

```bash
.venv/bin/python ops/private-read-admission/study-prep-20261027/portable-kernel-v1/build.py \
  --output "$PWD/data/artifacts/<new-private-bundle>"
```

The builder reads immutable Git objects, so it does not copy unrelated worktree
edits. A shallow clone must first fetch the pinned commit
`3077310593bdaa1f4ac0087d207d565936c73a6f` from this repository. Missing source,
changed SQL dependencies or a changed dependency lock refuse before output. The
builder performs no network request or package installation and does not require
the local PG experiment harness or any retained dataset.

Each reader subprocess used `python -I` from that environment. It inserted only
the built application directory, verified every source-manifest hash and checked
all loaded application modules came from the bundle. NumPy, pandas, torch,
scikit-learn, duckdb, polars, FastAPI and boto3 were absent. `PATH` was unusable
and subprocess creation was disabled before importing the kernel. The SQL JSON
was loaded directly. Earlier local fixture setup used Node; the final source
builder and the reader runtime require no Node installation.

The final proof ran four fresh reader processes against three new generated,
socket-only PostgreSQL clusters:

| Generated fixture | Reader processes | Verified service calls | Guard refusals |
| --- | ---: | ---: | ---: |
| Original 120k forecasts / 60k labels | 1 | 5 | 2 |
| Eligible 5,000-pair uncertainty regime | 2 | 10 | 4 |
| 5,001-pair capacity guard | 1 | 5 | 2 |

Each process called status, the fixed first forecast page, the fixed `SYNTH000`
filter, performance and a subsequent performance call. It then deliberately
changed the **supplied baseline**, not any database row: an incorrect schema
hash refused at the schema check and an incorrect row digest refused at the
fixture check. Both refusals preceded service/cache activity. These are **20
verified calls plus eight refused attempts / 28 reader connections**. They are
local experiment operations, not admissions borrowed from a live allowance.

The two eligible processes each showed an initially empty bootstrap cache, one
miss, then one hit. The second process independently started empty. The original
large case reported `insufficient_months`; the 5,001-pair case reported
`capacity_review_required`, with no bootstrap lookup in either case. Unchanged
performance response hashes matched within each process. Raw bodies, generated
rows and numerical uncertainty bounds are not retained in the packet.

## Preserved reader boundary

`snapshot_kernel.py` opens no connection and reads no credentials. A trusted
adapter supplies one prebound target and an already-open connection after its
own admission/presence proof. No HTTP request, environment URL, SQL text, query
parameter, offset, ticker or target discovery path is accepted by this component.
The filter is a separate fixed query; no caller supplies its value.

The kernel binds database/role/host/port, profile, complete schema digest, all
nine table counts/digests and SQL-contract identity. It keeps the controls,
permission audit, schema/data integrity, actual method, DTO serialization and
snapshot postcheck inside one repeatable-read, read-only transaction. All bounds
remain: three-second statements, 500-ms locks, five-second idle transactions and
ten-second transaction/application intervals. No result returns before confirmed
rollback, and all results retain `backend_absence_verified=false` and
`safe_for_public_release=false` for the outer controller to resolve.

Transaction-start, query, validation and rollback errors return fixed refusal
codes. The new transaction-start branch suppresses a driver-message context;
it was reviewed and unit-tested before the final isolated run. The builder
hash-checks three SQL text files plus their provenance companion and verifies
the exact original generation digest. The original four generation-source
commitments remain in that provenance record. The only
SQL derivative remains the separate candidate's 192 MiB relation guard; the
original P0 SQL and size cap stay unchanged.

Hosted target metadata requires a direct Singapore Neon hostname, a new
`study290_…` database and `study290_reader_…` role. Old P0 database/role names
refuse. The driver's TLS state, `sslmode=verify-full` and
`channel_binding=require` are checked before SQL. Those hosted branches have
boundary tests, **not an actual Neon/TLS deployment proof**. The local proof mode
requires the guarded private-socket namespace/owner/permissions and is included
in the target fingerprint; it cannot be reported as a hosted target. Its trust
authentication does not prove hosted credential separation.

Independent postflight rechecked the complete schema and all-nine-table digests
for every fixture, found zero reader backends and verified cleanup. All three
new databases/roles were dropped, servers stopped, cluster directories removed,
and the new minimal environment removed. Original resources, frozen artifacts,
consumed counts, expiry and source/publication gates were preserved.

The tests exercise target/driver mismatches, old-scope reuse, TLS/channel-binding
metadata, unknown/parameterized routes, early SQL-contract failure, redacted
transaction-start errors, output-path refusal, transitive SQL-source changes and
namespace-package closure. The actual PG run is additional to those unit checks.

## Evidence and limits

- [Final aggregate proof](../../../../data/artifacts/hosted-read-observations/portable-kernel-20261027/proof-02/summary.json)
- [Independent read-only audit](../../../../data/artifacts/hosted-read-observations/portable-kernel-20261027/independent-proof.json)
- [Hash-pinned dependencies](../../../../data/artifacts/hosted-read-observations/portable-kernel-20261027/bundle-07/requirements.lock)
- [Source manifest](../../../../data/artifacts/hosted-read-observations/portable-kernel-20261027/bundle-07/source-manifest.json)
- [Final runtime equivalence](../../../../data/artifacts/hosted-read-observations/portable-kernel-20261027/bundle-equivalence-self-contained.json)
- [Verification manifest](../../../../data/artifacts/hosted-read-observations/portable-kernel-20261027/verification-manifest.json)

Earlier candidate bundles and `proof-01` remain preserved. `proof-01` measured
`bundle-03`; `bundle-05` includes transaction-start redaction and the
builder's transitive-source checks and was actually rerun as `proof-02`.
`bundle-07` makes the SQL/dependency build inputs self-contained;
its runtime bytes match the measured candidate. The links above are private,
git-ignored workspace receipts, not public attachments.
No earlier result is relabeled as the final version.

Measurements are local samples after seeding, ANALYZE and baseline collection.
The kernel interval excludes connection opening, Python startup, HTTP,
authentication and admission/observer callbacks. Its service interval includes
DTO construction, so it is not identical to every earlier timing boundary.
SQLAlchemy execute counters omit driver initialization, transaction methods and
wire frames; they are not complete provider-operation budgets. No percentiles,
platform-cold or database-resume attribution, memory/CPU bound, Linux/Vercel ABI
fit, current free-quota headroom or production SLO is proved by this capture.

The bundle deliberately has no HTTP handler, target/baseline, role password,
callback credentials, initialized authority or live observation window. A hosted
adapter still needs fixed source-bound configuration, platform and application
authentication, single-use consume/ready callbacks, an observer whose PID cannot
be lost between callbacks, and independent reader/observer absence before release.
The existing executor/permit/plan accepts three routes. A new reviewed protocol
and study plan must explicitly cover the filter rather than disguise it as a
forecast page, silently edit the frozen scope or multiply an allowance across
profiles. Representative workloads, complete budgets and seven actual hosted
days remain unresolved. No new deployment or provider-backed operation occurred.
