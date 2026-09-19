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

## Safety contract

- Use a provider-created branch, disposable database, or isolated project with
  a different hostname from production. Do not point `pg_restore`, Alembic, or
  the API at the production URL.
- Keep connection URLs in environment variables or the provider's secret
  manager. Do not paste them into this runbook, shell history, CI logs, or an
  issue. Disable shell tracing (`set +x`) before running commands.
- Use the migration owner only for restoring and schema inspection. Use the
  read-only database and R2 roles for verification where possible.
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
export SOURCE_DATABASE_URL='...'       # private writer URL; never commit it
export RESTORE_DATABASE_URL='...'      # isolated target URL; not production
export AUDITOR_R2_ENDPOINT_URL='...'
export AUDITOR_R2_BUCKET='...'
export AUDITOR_R2_ACCESS_KEY_ID='...'
export AUDITOR_R2_SECRET_ACCESS_KEY='...'
export REHEARSAL_DIR="$(mktemp -d -t edgar-moe-restore)"
chmod 700 "$REHEARSAL_DIR"
```

If the provider offers a point-in-time branch, create it before setting
`RESTORE_DATABASE_URL`. Otherwise create a new empty database owned by the
migration role. Confirm the target hostname and database name twice before
continuing.

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
  export AUDITOR_DATABASE_URL="$SOURCE_DATABASE_URL"
  cd tools/evidence-auditor
  go run . -timeout 5m -stale-after 96h
) \
  >"$REHEARSAL_DIR/source-evidence-audit.json"
/usr/bin/time -p pg_dump --format=custom --no-owner \
  --file="$REHEARSAL_DIR/registry.dump" "$SOURCE_DATABASE_URL" \
  2>"$REHEARSAL_DIR/pg-dump.time.txt"
pg_restore --list "$REHEARSAL_DIR/registry.dump" \
  >"$REHEARSAL_DIR/restore-contents.txt"
/usr/bin/time -p pg_restore --no-owner --exit-on-error \
  --dbname="$RESTORE_DATABASE_URL" "$REHEARSAL_DIR/registry.dump" \
  2>"$REHEARSAL_DIR/pg-restore.time.txt"
date -u +%Y-%m-%dT%H:%M:%SZ | tee "$REHEARSAL_DIR/finished-at.txt"
```

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
credentials:

```bash
(
  export AUDITOR_DATABASE_URL="$RESTORE_DATABASE_URL"
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
```

The command exits `0` only when counts match, neither audit is incomplete, and
the restored target introduces no new finding. It exits `1` for a failed
comparison and `2` for malformed inputs.

If the production evidence is local-only, copy an immutable export to a private
isolated directory and use the auditor's `-manifest` and `-local-root` fixture
mode. The copy must be verified independently; the checked-in fixture is not a
production backup.

## Verify the read path

Start a local API process against the restored target, not the production URL:

```bash
EDGAR_MOE_REGISTRY_READ_DATABASE_URL="$RESTORE_DATABASE_URL" \
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
