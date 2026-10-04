# Postgres restore rehearsal

## Low-cost local rehearsal

The repository also includes a provider-neutral SQLite rehearsal for laptop or
CI smoke testing. It copies the registry through SQLite's consistent backup
API into a **new** destination, compares all forward-registry table counts, and
exercises the same aggregate read methods used by the API. It never overwrites
an existing destination or report and leaves the restored file available for
inspection.

This is useful evidence that the application can read an isolated local copy,
but it is deliberately **not** PostgreSQL/provider recovery evidence: it does
not test provider backups, R2/object bytes, the Go auditor, network credentials,
or an RTO. Run the production procedure below before making recovery claims.

```bash
set +x
REHEARSAL_DIR="$(mktemp -d -t edgar-moe-local-restore)"
chmod 700 "$REHEARSAL_DIR"
uv run python scripts/rehearse_sqlite_restore.py \
  --source data/forward/registry.sqlite3 \
  --destination "$REHEARSAL_DIR/restored.sqlite3" \
  --output "$REHEARSAL_DIR/restore-report.json"
```

Exit code `0` means table counts matched and the read probe completed. Exit
code `1` means the isolated copy completed but the comparison failed. Exit
code `2` means the command refused an unsafe/malformed input. Preserve the
destination and report when investigating a failure; the command does not
delete either one.

This runbook rehearses registry recovery in an isolated target. It is an
operational procedure, not evidence that a restore has already succeeded. The
maintainer records the outputs in a private incident/recovery note and never
uses the production database as the rehearsal target.

## Manual hosted workflow

For a repeatable hosted rehearsal, the repository includes the manual **Provider
isolated restore rehearsal** Actions workflow. It requires these repository
secrets, all kept outside logs and artifacts:

- `EDGAR_MOE_RESTORE_SOURCE_DATABASE_URL`: source URL with dump/read access;
- `EDGAR_MOE_RESTORE_TARGET_DATABASE_URL`: pre-created empty isolated target with
  restore/schema privileges;
- `EDGAR_MOE_RESTORE_SOURCE_AUDITOR_DATABASE_URL` and
  `EDGAR_MOE_RESTORE_TARGET_AUDITOR_DATABASE_URL`: corresponding SELECT-only
  URLs for the independent Go auditor and read-path probe;
- `EDGAR_MOE_R2_ENDPOINT_URL`, `EDGAR_MOE_R2_BUCKET`,
  `EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID`, and
  `EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY`: read-only R2 verification access.

The operator must choose `I_UNDERSTAND_ISOLATED_TARGET`. The workflow checks that
source and target configured endpoints differ, both auditor URLs point at their
matching database, and the target has no relations in non-system schemas before
it runs. It uses a custom
`pg_dump` only as a logical rehearsal, stores the dump under a private temporary
directory, removes it before job completion, and never uses `pg_restore --clean`.
The dump itself is never uploaded. Retained evidence contains counts, schema and
timing output, both Go audits, the read-path probe, a content-hashed comparison,
step outcomes, and a SHA-256 file list for 30 days. A successful workflow is
evidence of this specific source/target rehearsal; it is not proof that the
provider's managed backup job, RPO, or RTO meets a target until those observations
are recorded separately.

The identity preflight compares explicit TLS PostgreSQL URL hostnames, ports and
database names, then authenticates each URL in a bounded read-only transaction
and checks the observed database and session/current role. Only Neon's documented
`ep-…-pooler`/`ep-…` hostname pair is treated as the same endpoint; other aliases
must match exactly. A different database or port on the source hostname is not an
isolated target. Backend addresses (`inet_server_addr()`) are not stable provider
endpoint identifiers and are not used for pairing or isolation. These checks do
not independently prove provider branch topology: the operator must still verify
the disposable branch/project in the provider console before confirming dispatch.
See [PostgreSQL session information](https://www.postgresql.org/docs/current/functions-info.html)
and [Neon connection pooling](https://neon.com/docs/connect/connection-pooling).

The retained preflight report contains fixed failure codes, booleans, the empty
relation count and numeric server major versions, never URLs, hostnames, database
or role names, passwords or raw driver errors. For example,
`source_auditor_endpoint_mismatch` requires correcting that auditor secret, not
bypassing the check. Missing TLS/explicit credentials, connection-string endpoint
overrides, observed identity mismatches and nonempty targets all stop the run
before export/restore. A second all-non-system-schema emptiness check runs just
before restore. No check clears the target.

Database identities alone do not establish least privilege. Before exporting or
restoring, the workflow verifies the source auditor's effective SELECT-only grants
on `forward_runs` and `forward_artifacts`, and checks the target auditor's role
attributes and effective grants while the target has no user tables. After
restore, it requires SELECT on all eight forward-registry tables before either
the restored Go audit or application read probe can run. Optional SELECT on
`alembic_version` (migration metadata) is allowed; unrelated table reads, table
writes, role memberships, schema creation and executable user-defined
SECURITY DEFINER functions are rejected. These are bounded, read-only catalog
checks, not mutation probes or proof of future privileges/provider IAM.

Configure target-reader privileges **before** dispatch, using the isolated
restore owner. Since `pg_restore --no-privileges` does not copy source grants,
the target owner can set default SELECT privileges for tables it will create:

```sql
-- Run only in the explicitly isolated, empty target; replace the role names.
GRANT CONNECT ON DATABASE isolated_target TO isolated_reader;
GRANT USAGE ON SCHEMA public TO isolated_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE isolated_restore_owner IN SCHEMA public
  GRANT SELECT ON TABLES TO isolated_reader;
```

The reader must be a dedicated non-owner LOGIN role, without elevated role
attributes, other role memberships, database CREATE or schema CREATE. Do not
grant writes or sequence access. Default privileges are scoped to the exact
owner used by restore, not the source owner. The scoped exporter selects exactly
the eight public forward tables and `public.alembic_version`, even when the source
also contains unrelated relations. It never exports those other relations.
Restoring unrelated tables with these defaults fails the permission gate. A passing empty-target
check explicitly does **not** verify future SELECT grants; the separate
post-restore check is mandatory. A populated provider clone is not an empty
logical-restore target: use a new empty database instead, without clearing or
overwriting the clone.

Pull-request CI also runs a fully disposable PostgreSQL 16 version of this
exercise. It seeds an explicit synthetic fixture (only when the workflow passes
`--allow-synthetic`), creates a custom-format dump, restores it into a different
database, compares all registry-table counts, runs the Go auditor against the
restored database and local evidence, checks migrations, and probes the read
path. The uploaded report is useful regression evidence, but it does not replace
the provider-specific rehearsal below: it cannot validate a managed backup job,
R2 credentials, network policy, or production RPO/RTO.

## Safety contract

- Use a provider-created branch, disposable database, or isolated project with
  a different hostname from production. Do not point `pg_restore`, Alembic, or
  the API at the production URL.
- Keep connection URLs in environment variables or the provider's secret
  manager. Do not paste them into this runbook, shell history, CI logs, or an
  issue. Disable shell tracing (`set +x`) before running commands.
- Use the migration owner only for restoring and schema inspection. Use the
  dedicated read-only database and R2 roles for auditor/read-path verification.
- The rehearsal must not change forecasts, labels, model identities, or source
  artifacts. Any mismatch is recorded and investigated; it is not repaired by
  editing the restored data.

## Prerequisites

Install or make available on a trusted machine:

- a provider backup/export and its backup identifier;
- PostgreSQL client tools (`pg_restore`, `psql`, and optionally `createdb`);
- this repository with the reviewed commit checked out;
- the Go evidence auditor and a read-only R2 credential, if production evidence
  is mirrored to R2.

Define these variables without printing them:

```bash
set +x
export SOURCE_DATABASE_URL='...'       # private dump/read URL; never commit it
export RESTORE_DATABASE_URL='...'      # isolated restore-owner URL; not production
export SOURCE_AUDITOR_DATABASE_URL='...'  # SELECT-only on the two evidence tables
export RESTORE_AUDITOR_DATABASE_URL='...' # isolated SELECT-only registry reader
export AUDITOR_R2_ENDPOINT_URL='...'
export AUDITOR_R2_BUCKET='...'
export AUDITOR_R2_ACCESS_KEY_ID='...'
export AUDITOR_R2_SECRET_ACCESS_KEY='...'
export REHEARSAL_DIR="$(mktemp -d -t edgar-moe-restore)"
chmod 700 "$REHEARSAL_DIR"
```

For the logical dump below, create a new empty database owned by the migration
role and configure the separate target-reader/default privileges described
above. Do not restore a dump over a populated point-in-time clone. A provider's
managed point-in-time recovery is a separate operation. Confirm the target
hostname and database name twice before continuing; independently verify that
both auditor URLs point to their corresponding source/isolated databases.

Before any dump/restore, retain these permission reports and stop if either
command fails:

```bash
AUDITOR_DATABASE_URL="$SOURCE_AUDITOR_DATABASE_URL" \
  uv run python scripts/verify_postgres_auditor.py --profile evidence \
  > "$REHEARSAL_DIR/source-permissions.json"
AUDITOR_DATABASE_URL="$RESTORE_AUDITOR_DATABASE_URL" \
  uv run python scripts/verify_postgres_auditor.py --profile empty-restore-target \
  > "$REHEARSAL_DIR/empty-target-permissions.json"
```

## Capture a backup and restore it

Use the provider's supported export mechanism. For a self-managed PostgreSQL
instance, a custom-format dump is suitable:

```bash
date -u +%Y-%m-%dT%H:%M:%SZ | tee "$REHEARSAL_DIR/started-at.txt"
psql "$SOURCE_DATABASE_URL" -XAtc \
  "SELECT jsonb_build_object(
    'forward_datasets', (SELECT count(*) FROM forward_datasets),
    'forward_models', (SELECT count(*) FROM forward_models),
    'forward_runs', (SELECT count(*) FROM forward_runs),
    'forward_forecasts', (SELECT count(*) FROM forward_forecasts),
    'forward_labels', (SELECT count(*) FROM forward_labels),
    'forward_artifacts', (SELECT count(*) FROM forward_artifacts),
    'forward_data_quality_checks', (SELECT count(*) FROM forward_data_quality_checks),
    'forward_audit_events', (SELECT count(*) FROM forward_audit_events)
  )::text" | tee "$REHEARSAL_DIR/source-counts.json"
(
  export AUDITOR_DATABASE_URL="$SOURCE_AUDITOR_DATABASE_URL"
  cd tools/evidence-auditor
  go run . -timeout 5m -stale-after 96h
) \
  >"$REHEARSAL_DIR/source-evidence-audit.json"
SOURCE_DATABASE_URL="$SOURCE_DATABASE_URL" \
  uv run python scripts/export_registry_dump.py \
  --output "$REHEARSAL_DIR/registry.dump" \
  --functions-output "$REHEARSAL_DIR/registry-functions.sql" \
  > "$REHEARSAL_DIR/dump-scope.json" \
  2>"$REHEARSAL_DIR/pg-dump.time.txt"
pg_restore --list "$REHEARSAL_DIR/registry.dump" \
  >"$REHEARSAL_DIR/restore-contents.txt"
# Stop on any function installation error; never continue to pg_restore.
psql "$RESTORE_DATABASE_URL" -X --set ON_ERROR_STOP=1 \
  --file="$REHEARSAL_DIR/registry-functions.sql" && \
/usr/bin/time -p pg_restore --no-owner --no-privileges --exit-on-error \
  --dbname="$RESTORE_DATABASE_URL" "$REHEARSAL_DIR/registry.dump" \
  2>"$REHEARSAL_DIR/pg-restore.time.txt"
date -u +%Y-%m-%dT%H:%M:%SZ | tee "$REHEARSAL_DIR/finished-at.txt"
```

The exporter currently supports reviewed migration `20260921_0002` only. It
rejects missing/partitioned registry tables, altered or extra registry triggers,
trigger-function drift, external foreign keys, and custom/default/generated
column dependencies. It requires private output directories and never
overwrites an existing output. Inspection and dump share one exported read-only
snapshot, held open until `pg_dump` finishes. Table-filtered `pg_dump` omits function
dependencies, so the SQL sidecar contains only the two append-only functions
from the pinned migration implementation; install it before restoring the
archive's tables, indexes, foreign keys, and triggers. Keep source schema changes
paused throughout export. This is **registry recovery**, not a complete backup
of a shared database. CI tests exclusion and restored immutability using only
synthetic, disposable PostgreSQL data. Keep both raw outputs private and remove
them after the rehearsal; the hosted workflow retains only its redacted scope
report and deletes the entire private temporary directory.

For a provider-managed backup, replace only the dump/restore commands with the
provider's isolated restore operation and retain its job ID, start/end times,
and reported size in the rehearsal record. Never use `--clean` against a shared
or production database.

## Verify schema and row counts

Run migrations in check-only mode. This must not require a schema change on the
restored target:

```bash
EDGAR_MOE_REGISTRY_DATABASE_URL="$RESTORE_DATABASE_URL" \
  uv run alembic check | tee "$REHEARSAL_DIR/alembic-check.txt"
```

Capture deterministic counts for every forward-registry table:

```bash
psql "$RESTORE_DATABASE_URL" -XAtc \
  "SELECT jsonb_build_object(
    'forward_datasets', (SELECT count(*) FROM forward_datasets),
    'forward_models', (SELECT count(*) FROM forward_models),
    'forward_runs', (SELECT count(*) FROM forward_runs),
    'forward_forecasts', (SELECT count(*) FROM forward_forecasts),
    'forward_labels', (SELECT count(*) FROM forward_labels),
    'forward_artifacts', (SELECT count(*) FROM forward_artifacts),
    'forward_data_quality_checks', (SELECT count(*) FROM forward_data_quality_checks),
    'forward_audit_events', (SELECT count(*) FROM forward_audit_events)
  )::text" | tee "$REHEARSAL_DIR/restored-counts.json"
```

Compare the result with a count export captured from the source backup at the
same point in time. A mismatch stops the rehearsal; do not infer that a missing
row is harmless because the API still starts.

## Verify content-addressed evidence

The Go auditor reads a consistent registry snapshot and verifies every artifact
reference and object byte. Run it with the isolated database and read-only R2
credentials. First retain the post-restore grant report; stop on a failure
before running either the auditor or API probe:

```bash
AUDITOR_DATABASE_URL="$RESTORE_AUDITOR_DATABASE_URL" \
  uv run python scripts/verify_postgres_auditor.py --profile registry-reader \
  > "$REHEARSAL_DIR/restored-target-permissions.json"
(
  export AUDITOR_DATABASE_URL="$RESTORE_AUDITOR_DATABASE_URL"
  cd tools/evidence-auditor
  go run . -timeout 5m -stale-after 96h
) \
  >"$REHEARSAL_DIR/evidence-audit.json"
```

Record both auditor exit codes and inspect `status`, `objects_verified`, and every
finding. A restored target must not introduce any new `missing_object`,
`hash_mismatch`, `size_mismatch`, `invalid_*`, or `missing_batch_evidence` finding.
An existing `failed_run` or other operational finding is acceptable only when it
is identical to the source audit and is explained in the recovery record. Any
new integrity finding or an incomplete audit (exit code `2`) fails the rehearsal.

Run the deterministic comparison gate and retain its output:

```bash
uv run python scripts/compare_restore_reports.py \
  --source-counts "$REHEARSAL_DIR/source-counts.json" \
  --restored-counts "$REHEARSAL_DIR/restored-counts.json" \
  --source-audit "$REHEARSAL_DIR/source-evidence-audit.json" \
  --restored-audit "$REHEARSAL_DIR/evidence-audit.json" \
  | tee "$REHEARSAL_DIR/restore-comparison.json"
uv run python scripts/verify_restore_comparison.py \
  --report "$REHEARSAL_DIR/restore-comparison.json"
```

The command exits `0` only when counts match, neither audit is incomplete, and
the restored target introduces no new finding. It exits `1` for a failed
comparison and `2` for malformed inputs. The comparison report includes a
content SHA-256; the verifier proves that the retained JSON was not altered
after the comparison ran.

If the production evidence is local-only, copy an immutable export to a private
isolated directory and use the auditor's `-manifest` and `-local-root` fixture
mode. The copy must be verified independently; the checked-in fixture is not a
production backup.

## Verify the read path

Start a local API process against the restored target, not the production URL:

```bash
EDGAR_MOE_REGISTRY_READ_DATABASE_URL="$RESTORE_AUDITOR_DATABASE_URL" \
  EDGAR_MOE_REGISTRY_DATABASE_URL='' \
  uv run uvicorn edgar_moe.api.app:app --host 127.0.0.1 --port 8000
```

In a second terminal, query the status and one paginated read endpoint. Stop if
the API reports the registry as disconnected, returns a 5xx, or exposes a
different count from the restored SQL snapshot. Keep the API process local and
terminate it before destroying the target database.

## Recovery record and acceptance

Save a private record containing:

1. repository commit and provider backup/restore job identifier;
2. isolated target identifier (never its password or full URL);
3. UTC start/end timestamps and elapsed restore time;
4. source and restored table-count JSON, plus any mismatch explanation;
5. Alembic check output and both complete Go auditor JSON reports;
6. API status/read-path result and any missing evidence;
7. target teardown time and the operator who reviewed the result.

The rehearsal passes only when schema checks, counts, the evidence auditor, and
the local read path all agree. Record measured restore time as an observation;
do not turn one rehearsal into an RTO promise. Repeat after material schema,
provider, or artifact-storage changes and at least quarterly while the forward
system is operated.

## Safe response to a failed rehearsal

Preserve the target and all reports. Do not delete registry rows or rewrite
forecast/label payloads. For a missing or corrupt object, compare the expected
content-addressed SHA-256 with the private source/mirror and re-mirror the exact
bytes only after independent verification. For a failed run with missing batch
evidence, keep the failed identity, investigate the original error, and retry as
a new run; never convert the failed run into a success.
