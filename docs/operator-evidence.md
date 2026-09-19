# Operator evidence packets

The repository can verify the software contract for provider operations, but it
cannot observe a hosted database grant, a managed-backup schedule, or an actual
object-store outage from CI alone. Keep those observations as a redacted,
content-addressed operator packet instead of copying credentials, connection
URLs, raw dumps, or provider payloads into the repository.

## Create and verify a packet

Start with the example draft, replace the `not_run` records with the checks you
actually performed, and add one artifact entry for every retained redacted
report. An artifact name is a local evidence filename; the builder below derives
its SHA-256 and byte count from the retained file, rather than requiring those
values to be copied by hand. The builder also requires the artifact directory to
contain exactly the declared files and rejects symlinks, oversized files, and
credential-bearing content.

```bash
uv run python scripts/build_operator_evidence_packet.py \
  --input /path/to/provider-packet-draft.json \
  --artifact-root /path/to/redacted-artifacts \
  --output /tmp/operator-evidence-packet.json
```

If the artifact metadata was already computed and independently checked, the
lower-level writer remains available as an alternative to the builder:

```bash
uv run python scripts/write_operator_evidence_packet.py \
  --input docs/operator-evidence-packet.example.json \
  --output /tmp/operator-evidence-packet-manual.json
```

Verify the packet produced by the builder (or substitute the manual writer's
path when using that alternative):

```bash
uv run python scripts/verify_operator_evidence_packet.py \
  --packet /tmp/operator-evidence-packet.json

uv run python scripts/check_operator_readiness.py \
  --packet /tmp/operator-evidence-packet.json \
  --max-age-days 30
```

The builder and writer validate the draft, add `packet_sha256`, and publish the
finished file atomically without overwriting an existing packet. The verifier
checks the hash, timestamps, check coverage, artifact hashes, and redaction
contract without printing packet contents. It rejects URLs, connection strings,
secret-like assignments, private-key material, absolute/path-traversal artifact
names, unknown fields, and passed/failed checks without evidence references.

The readiness command is a separate, read-only release aid. It requires the
three P0 provider checks (`database_least_privilege`, `restore_rehearsal`, and
`partial_write_reconciliation`) to be explicitly `passed`, backed by evidence
references, and observed within the requested freshness window. It exits `0`
only for `ready`; incomplete evidence returns `blocked`, and old otherwise
passed evidence returns `stale`. It never changes packet status or creates
provider evidence.

CI also runs `scripts/validate_provider_workflows.py`. This static gate keeps
the provider workflows manual-only with `contents: read`, preserves in-flight
audit runs, confines secret expressions to environment mappings, requires
redaction and SHA-256 hashing before artifact upload, and rejects writer or
repair paths in the read-only R2 audit. The isolated restore workflow must retain
its explicit confirmation and source/target safety checks. These are repository
controls, not evidence that the provider workflows have been run.

## Required operator evidence

The packet is a record format, not proof by itself. For the current roadmap,
retain a packet after these provider-side exercises:

| Check | Required observation |
| --- | --- |
| `database_least_privilege` | The deployed API reader can perform the intended reads, cannot mutate the registry, and uses a bounded connection policy. |
| `restore_rehearsal` | An isolated target is restored without overwriting production; source/target identity, counts, schema state, read-path probe, and elapsed timing are recorded. |
| `partial_write_reconciliation` | A production-like object-store failure leaves the primary reference intact and a later reconciliation repairs the mirror idempotently. |

P1 checks (`deployment_smoke`, `alert_delivery`, `capacity_baseline`, and
`drift_history`) may be included in the same packet or a later packet. A
`passed` or `failed` check must reference retained redacted artifacts; a
`not_run` or `not_applicable` check must explain why it has no evidence.

Never use a packet to claim a provider control was completed when the check is
`not_run`, when the evidence is synthetic, or when the provider-side observation
was not independently reviewed.
