# Operator evidence packets

The repository can verify the software contract for provider operations, but it
cannot observe a hosted database grant, a managed-backup schedule, or an actual
object-store outage from CI alone. Keep those observations as a redacted,
content-addressed operator packet instead of copying credentials, connection
URLs, raw dumps, or provider payloads into the repository.

## Create and verify a packet

Start with the example draft, replace the `not_run` records with the checks you
actually performed, and add one artifact entry for every retained redacted
report. An artifact name is a local evidence filename; its SHA-256 is the hash
of the retained file, not of a database dump or secret.

```bash
uv run python scripts/write_operator_evidence_packet.py \
  --input docs/operator-evidence-packet.example.json \
  --output /tmp/operator-evidence-packet.json

uv run python scripts/verify_operator_evidence_packet.py \
  --packet /tmp/operator-evidence-packet.json
```

The writer adds `packet_sha256` after validating the draft. The verifier checks
the hash, timestamps, check coverage, artifact hashes, and redaction contract
without printing packet contents. It rejects URLs, connection strings,
secret-like assignments, private-key material, absolute/path-traversal artifact
names, unknown fields, and passed/failed checks without evidence references.

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
