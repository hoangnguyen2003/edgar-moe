# Go evidence auditor

`tools/evidence-auditor` is a deliberately small, read-only operational tool.
It verifies that structured forward-registry records still have the content-
addressed evidence objects they reference. It is an independent implementation
of the cross-system check, so it does not import the Python research or web
runtime.

## What it checks

- every registered artifact has a valid `local://` or `r2://` content-addressed
  URI whose key agrees with the recorded SHA-256;
- every artifact object can be read within the configured size limit and its
  byte count and SHA-256 match the registry;
- every artifact refers to a known run and has a unique ID;
- succeeded or failed forecast/settlement runs have their expected batch
  evidence;
- failed runs, stale running runs, invalid timestamps, unknown statuses, and
  other reconciliation findings.

The command only reads. It never writes the database, uploads objects, repairs
references, deletes evidence, or changes run status. A failed run is evidence to
investigate—not permission to erase and retry.

## Live audit

Use a database role that has only `SELECT` on `forward_runs` and
`forward_artifacts` (and no DDL or write privileges), plus an R2 credential that
can only read the evidence bucket. The Go transaction is explicitly read-only,
but that is defense in depth; database grants must enforce the boundary.

```sh
AUDITOR_DATABASE_URL='postgresql://reader:...?...sslmode=require' \
AUDITOR_R2_ENDPOINT_URL='https://<account-id>.r2.cloudflarestorage.com' \
AUDITOR_R2_BUCKET='private-evidence' \
AUDITOR_R2_ACCESS_KEY_ID='read-only-key' \
AUDITOR_R2_SECRET_ACCESS_KEY='read-only-secret' \
go run ./tools/evidence-auditor
```

The default output is one JSON report. Exit codes are:

- `0`: all records and referenced objects verified;
- `1`: integrity or operational findings were detected;
- `2`: the audit could not complete (configuration, registry, object-store,
  argument, or output failure).

`-stale-after` defaults to six hours. `-max-object-bytes` defaults to 64 MiB and
prevents an unexpected object from consuming unbounded memory or time. Objects
are streamed through SHA-256; the auditor does not load an artifact into memory.

## Repeatable provider audit

The repository also includes a manual GitHub Actions workflow named **Provider R2
evidence audit**. Configure these Actions secrets with read-only credentials:

- `EDGAR_MOE_REGISTRY_AUDITOR_DATABASE_URL`
- `EDGAR_MOE_R2_ENDPOINT_URL`
- `EDGAR_MOE_R2_BUCKET`
- `EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID`
- `EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY`

Run it from the Actions tab after a provider change, suspected partial write, or
before relying on a restore. The workflow never enables the Go repair path or
uses the runner's writer/R2 upload credentials. It retains the JSON result, a
redacted error stream, run metadata, and a SHA-256 file list for 30 days. Before
upload, it scans every retained text file for credential-bearing URLs, token
patterns, private-key material, unsafe symlinks, and unexpected binary payloads;
a scan failure blocks the upload. It then fails the job when the audit reports an
integrity finding or a required secret is missing. A passing disposable CI
fixture still does not prove the hosted bucket was checked.

## Offline contract fixture

For CI and local development, `-manifest` reads a complete JSON export with
`schema_version: 1`, `runs`, and `artifacts`; `-local-root` serves the referenced
`local://` objects from an `os.Root`-confined directory:

```sh
go run ./tools/evidence-auditor \
  -manifest tools/evidence-auditor/testdata/manifest.json \
  -local-root tools/evidence-auditor/testdata
```

The checked-in golden fixture is also consumed by a Python test. That test proves
the Go and Python implementations agree on the URI, SHA-256, and byte-count
contract. The offline manifest is a test fixture, not a production backup export.

## Failure response

For the complete isolated database restore and read-path rehearsal, follow the
[restore-rehearsal runbook](restore-rehearsal.md) before treating a backup as
recovery evidence.

Use the report to decide whether the issue is a missing mirror object, a hash or
size mismatch, a partially completed run, or an unavailable dependency. Do not
automatically reconcile by deleting rows or overwriting objects. For a confirmed
partial write, preserve the run and follow the recovery procedure in
`docs/architecture-roadmap.md`; an operator may re-mirror the exact bytes only
after independently verifying their source and digest.

## Why Go belongs here

Go is used for an independent, low-dependency integrity boundary—not for model
training or web serving. Its binary can run in a minimal operations job and has
no access to the Python model runtime. This adds a maintenance cost, so the tool
stays narrow, versioned by a separate `go.mod`, and covered by race-enabled tests.
